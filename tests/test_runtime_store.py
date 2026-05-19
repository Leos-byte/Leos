from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from leos_agent import ActionStep, Goal, TransactionPlan
from leos_agent.runtime_store import InMemoryRuntimeStore, JsonlRuntimeStore, RuntimeStoreError
from leos_agent.tools import Secret


class RuntimeStoreTests(unittest.TestCase):
    def test_in_memory_save_load_goal(self) -> None:
        store = InMemoryRuntimeStore()
        goal = Goal("demo", ["done"])

        store.save_goal(goal)

        self.assertEqual(store.load_goal(goal.goal_id), goal)

    def test_in_memory_save_load_plan(self) -> None:
        store = InMemoryRuntimeStore()
        plan = TransactionPlan(Goal("demo", ["done"]), [ActionStep("echo", {"message": "hi"}, "echo")])

        store.save_plan(plan)

        self.assertEqual(store.load_plan(plan.plan_id), plan)

    def test_in_memory_append_list_events(self) -> None:
        store = InMemoryRuntimeStore()

        store.append_runtime_event({"goal_id": "g1", "event_type": "one"})
        store.append_runtime_event({"goal_id": "g2", "event_type": "two"})

        self.assertEqual(len(store.list_runtime_events()), 2)
        self.assertEqual(len(store.list_runtime_events("g1")), 1)

    def test_in_memory_checkpoint_save_load(self) -> None:
        store = InMemoryRuntimeStore()

        store.save_checkpoint("k", {"value": 1})

        self.assertEqual(store.load_checkpoint("k"), {"value": 1})

    def test_jsonl_save_load_goal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonlRuntimeStore(Path(tmp))
            goal = Goal("demo", ["done"], stop_conditions=["stop"])

            store.save_goal(goal)
            loaded = store.load_goal(goal.goal_id)

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.description, "demo")
            self.assertEqual(loaded.goal_id, goal.goal_id)

    def test_jsonl_save_load_plan_basic_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonlRuntimeStore(Path(tmp))
            plan = TransactionPlan(Goal("demo", ["done"]), [ActionStep("echo", {"message": "hi"}, "echo")])

            store.save_plan(plan)
            loaded = store.load_plan(plan.plan_id)

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.plan_id, plan.plan_id)
            self.assertEqual(loaded.steps[0].tool_name, "echo")

    def test_jsonl_append_list_events_by_goal_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonlRuntimeStore(Path(tmp))

            store.append_runtime_event({"goal_id": "g1", "event_type": "one"})
            store.append_runtime_event({"goal_id": "g2", "event_type": "two"})

            self.assertEqual(len(store.list_runtime_events("g1")), 1)

    def test_jsonl_checkpoint_save_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonlRuntimeStore(Path(tmp))

            store.save_checkpoint("k", {"value": 1})

            self.assertEqual(store.load_checkpoint("k"), {"value": 1})

    def test_secret_not_saved_to_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonlRuntimeStore(Path(tmp))

            with self.assertRaises(RuntimeStoreError):
                store.save_checkpoint("k", {"token": Secret("secret-value")})

    def test_secret_not_saved_to_event(self) -> None:
        store = InMemoryRuntimeStore()

        with self.assertRaises(RuntimeStoreError):
            store.append_runtime_event({"token": Secret("secret-value")})

    def test_missing_goal_returns_none(self) -> None:
        self.assertIsNone(InMemoryRuntimeStore().load_goal("missing"))

    def test_corrupt_jsonl_line_raises_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "events.jsonl").write_text("{bad json\n", encoding="utf-8")
            store = JsonlRuntimeStore(path)

            with self.assertRaises(RuntimeStoreError):
                store.list_runtime_events()


if __name__ == "__main__":
    unittest.main()
