"""Task queue and watchdog primitives for long-running runtime work."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .audit import AuditLog
from .enums import GoalStatus, TaskStatus
from .plans import TransactionPlan


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")


@dataclass(frozen=True)
class TimeoutPolicy:
    heartbeat_timeout_seconds: Optional[float] = 60.0
    runtime_timeout_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        for name in ("heartbeat_timeout_seconds", "runtime_timeout_seconds"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass
class RuntimeTask:
    plan: TransactionPlan
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: TaskStatus = TaskStatus.QUEUED
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_policy: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    idempotency_key: Optional[str] = None
    attempts: int = 0
    locked_by: Optional[str] = None
    enqueued_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    last_heartbeat_at: Optional[float] = None
    finished_at: Optional[float] = None
    failure_reason: Optional[str] = None

    def __post_init__(self) -> None:
        self.status = TaskStatus(self.status)

    @property
    def active(self) -> bool:
        return self.status in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.PAUSED}


class TaskQueue:
    """In-memory FIFO queue with audit events and idempotency deduplication."""

    def __init__(self, audit_log: Optional[AuditLog] = None) -> None:
        self.audit_log = audit_log or AuditLog()
        self._tasks: Dict[str, RuntimeTask] = {}
        self._order: List[str] = []
        self._idempotency_index: Dict[str, str] = {}

    def enqueue(
        self,
        plan: TransactionPlan,
        *,
        idempotency_key: Optional[str] = None,
        retry_policy: Optional[RetryPolicy] = None,
        timeout_policy: Optional[TimeoutPolicy] = None,
    ) -> RuntimeTask:
        if idempotency_key and idempotency_key in self._idempotency_index:
            existing = self._tasks[self._idempotency_index[idempotency_key]]
            self.audit_log.record(
                "task.deduplicated",
                "Task idempotency key already exists",
                task_id=existing.task_id,
                plan_id=existing.plan.plan_id,
                idempotency_key=idempotency_key,
                status=existing.status.value,
            )
            return existing

        task = RuntimeTask(
            plan=plan,
            retry_policy=retry_policy or RetryPolicy(),
            timeout_policy=timeout_policy or TimeoutPolicy(),
            idempotency_key=idempotency_key,
        )
        self._tasks[task.task_id] = task
        self._order.append(task.task_id)
        if idempotency_key:
            self._idempotency_index[idempotency_key] = task.task_id
        self.audit_log.record(
            "task.enqueued",
            "Task enqueued",
            task_id=task.task_id,
            plan_id=plan.plan_id,
            idempotency_key=idempotency_key,
        )
        return task

    def claim(self, worker_id: str, *, now: Optional[float] = None) -> Optional[RuntimeTask]:
        timestamp = time.time() if now is None else now
        for task_id in self._order:
            task = self._tasks[task_id]
            if task.status is not TaskStatus.QUEUED:
                continue
            task.status = TaskStatus.RUNNING
            task.locked_by = worker_id
            task.attempts += 1
            task.started_at = timestamp
            task.last_heartbeat_at = timestamp
            self.audit_log.record(
                "task.claimed",
                "Task claimed by worker",
                task_id=task.task_id,
                plan_id=task.plan.plan_id,
                worker_id=worker_id,
                attempts=task.attempts,
            )
            return task
        return None

    def heartbeat(self, task_id: str, worker_id: str, *, now: Optional[float] = None) -> RuntimeTask:
        task = self._require_task(task_id)
        self._require_lock(task, worker_id)
        task.last_heartbeat_at = time.time() if now is None else now
        self.audit_log.record("task.heartbeat", "Task heartbeat recorded", task_id=task.task_id, worker_id=worker_id)
        return task

    def complete(self, task_id: str, worker_id: str, *, now: Optional[float] = None) -> RuntimeTask:
        task = self._finish(task_id, worker_id, TaskStatus.SUCCEEDED, now=now)
        self.audit_log.record("task.completed", "Task completed", task_id=task.task_id, worker_id=worker_id)
        return task

    def fail(self, task_id: str, worker_id: str, reason: str, *, now: Optional[float] = None) -> RuntimeTask:
        task = self._finish(task_id, worker_id, TaskStatus.FAILED, now=now)
        task.failure_reason = reason
        self.audit_log.record("task.failed", reason, task_id=task.task_id, worker_id=worker_id)
        return task

    def retry(self, task_id: str, worker_id: str, reason: str) -> RuntimeTask:
        task = self._require_task(task_id)
        self._require_lock(task, worker_id)
        task.status = TaskStatus.QUEUED
        task.locked_by = None
        task.started_at = None
        task.last_heartbeat_at = None
        task.finished_at = None
        task.failure_reason = reason
        self.audit_log.record(
            "task.retry_scheduled",
            reason,
            task_id=task.task_id,
            worker_id=worker_id,
            attempts=task.attempts,
            max_attempts=task.retry_policy.max_attempts,
        )
        return task

    def cancel(self, task_id: str, *, reason: str = "cancelled", now: Optional[float] = None) -> RuntimeTask:
        task = self._require_task(task_id)
        task.status = TaskStatus.CANCELLED
        task.finished_at = time.time() if now is None else now
        task.failure_reason = reason
        task.locked_by = None
        self.audit_log.record("task.cancelled", reason, task_id=task.task_id)
        return task

    def pause(self, task_id: str, worker_id: str) -> RuntimeTask:
        task = self._require_task(task_id)
        self._require_lock(task, worker_id)
        task.status = TaskStatus.PAUSED
        task.locked_by = None
        self.audit_log.record("task.paused", "Task paused", task_id=task.task_id, worker_id=worker_id)
        return task

    def resume(self, task_id: str) -> RuntimeTask:
        task = self._require_task(task_id)
        if task.status is not TaskStatus.PAUSED:
            raise ValueError("Only paused tasks can be resumed")
        task.status = TaskStatus.QUEUED
        self.audit_log.record("task.resumed", "Task resumed", task_id=task.task_id)
        return task

    def get(self, task_id: str) -> RuntimeTask:
        return self._require_task(task_id)

    def tasks(self) -> List[RuntimeTask]:
        return [self._tasks[task_id] for task_id in self._order]

    def _finish(self, task_id: str, worker_id: str, status: TaskStatus, *, now: Optional[float]) -> RuntimeTask:
        task = self._require_task(task_id)
        self._require_lock(task, worker_id)
        task.status = status
        task.finished_at = time.time() if now is None else now
        task.locked_by = None
        return task

    def _require_task(self, task_id: str) -> RuntimeTask:
        if task_id not in self._tasks:
            raise KeyError(f"Unknown task: {task_id}")
        return self._tasks[task_id]

    @staticmethod
    def _require_lock(task: RuntimeTask, worker_id: str) -> None:
        if task.status is not TaskStatus.RUNNING or task.locked_by != worker_id:
            raise PermissionError("Task is not locked by this worker")


class Watchdog:
    """Detects timed-out running tasks using heartbeat/runtime limits."""

    def __init__(self, queue: TaskQueue, audit_log: Optional[AuditLog] = None) -> None:
        self.queue = queue
        self.audit_log = audit_log or queue.audit_log

    def check(self, *, now: Optional[float] = None) -> List[RuntimeTask]:
        timestamp = time.time() if now is None else now
        timed_out = []
        for task in self.queue.tasks():
            if task.status is not TaskStatus.RUNNING:
                continue
            reason = self._timeout_reason(task, timestamp)
            if not reason:
                continue
            task.status = TaskStatus.TIMED_OUT
            task.finished_at = timestamp
            task.failure_reason = reason
            task.locked_by = None
            timed_out.append(task)
            self.audit_log.record(
                "task.timed_out",
                reason,
                task_id=task.task_id,
                plan_id=task.plan.plan_id,
                attempts=task.attempts,
            )
        return timed_out

    @staticmethod
    def _timeout_reason(task: RuntimeTask, now: float) -> Optional[str]:
        heartbeat_timeout = task.timeout_policy.heartbeat_timeout_seconds
        if heartbeat_timeout is not None and task.last_heartbeat_at is not None:
            if now - task.last_heartbeat_at > heartbeat_timeout:
                return "Task heartbeat timed out"
        runtime_timeout = task.timeout_policy.runtime_timeout_seconds
        if runtime_timeout is not None and task.started_at is not None:
            if now - task.started_at > runtime_timeout:
                return "Task runtime timed out"
        return None


class TaskRunner:
    """Claims queued tasks and executes their plans through an AgentKernel-like object."""

    def __init__(self, queue: TaskQueue, kernel: object, *, worker_id: str = "worker", audit_log: Optional[AuditLog] = None) -> None:
        self.queue = queue
        self.kernel = kernel
        self.worker_id = worker_id
        self.audit_log = audit_log or queue.audit_log

    def run_next(self, *, now: Optional[float] = None) -> Optional[RuntimeTask]:
        task = self.queue.claim(self.worker_id, now=now)
        if task is None:
            self.audit_log.record("task.runner_idle", "No queued task available", worker_id=self.worker_id)
            return None

        self.audit_log.record(
            "task.runner_started",
            "Task runner started execution",
            task_id=task.task_id,
            plan_id=task.plan.plan_id,
            worker_id=self.worker_id,
        )
        try:
            self.kernel.run(task.plan)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - task runner must capture task-level failures
            return self._handle_failure(task, f"Task execution raised: {exc}", now=now)

        goal_status = task.plan.goal.status
        self.audit_log.record(
            "task.runner_finished",
            "Task runner finished execution",
            task_id=task.task_id,
            plan_id=task.plan.plan_id,
            worker_id=self.worker_id,
            goal_status=goal_status.value,
        )
        if goal_status is GoalStatus.SUCCEEDED:
            return self.queue.complete(task.task_id, self.worker_id, now=now)
        return self._handle_failure(task, f"Goal ended with status {goal_status.value}", now=now)

    def _handle_failure(self, task: RuntimeTask, reason: str, *, now: Optional[float]) -> RuntimeTask:
        if task.attempts < task.retry_policy.max_attempts:
            return self.queue.retry(task.task_id, self.worker_id, reason)
        return self.queue.fail(task.task_id, self.worker_id, reason, now=now)
