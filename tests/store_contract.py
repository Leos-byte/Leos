"""Shared RuntimeStore contract test mixin.

Any store implementing the `RuntimeStore` protocol should satisfy these
invariants. Concrete test classes multiply-inherit
``(RuntimeStoreContract, unittest.TestCase)`` and implement ``make_store``.
Durable stores override ``reopen`` to return a fresh instance over the same
backing so restart-recovery is exercised.
"""

from __future__ import annotations

from typing import Any

from leos_agent import ActionStep, Goal, TransactionPlan
from leos_agent.runtime_store import RuntimeStoreError
from leos_agent.tools import Secret


class RuntimeStoreContract:
    def make_store(self) -> Any:
        raise NotImplementedError

    def reopen(self, store: Any) -> Any:
        """Return a store seeing the same data. Durable stores override this."""
        return store

    def test_save_and_load_goal_round_trip(self) -> None:
        store = self.make_store()
        goal = Goal("demo goal", ["done"])
        store.save_goal(goal)
        loaded = self.reopen(store).load_goal(goal.goal_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.description, "demo goal")  # type: ignore[union-attr]

    def test_goal_latest_write_wins(self) -> None:
        store = self.make_store()
        goal = Goal("first", ["done"])
        store.save_goal(goal)
        store.save_goal(Goal("second", ["done"], goal_id=goal.goal_id))
        loaded = self.reopen(store).load_goal(goal.goal_id)
        self.assertEqual(loaded.description, "second")  # type: ignore[union-attr]

    def test_save_and_load_plan_round_trip(self) -> None:
        store = self.make_store()
        goal = Goal("demo", ["done"])
        plan = TransactionPlan(goal, [ActionStep("echo", {"message": "hi"}, "echo")])
        store.save_plan(plan)
        loaded = self.reopen(store).load_plan(plan.plan_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.steps[0].tool_name, "echo")  # type: ignore[union-attr]

    def test_events_append_only_and_ordered(self) -> None:
        store = self.make_store()
        store.append_runtime_event({"goal_id": "g", "event_type": "one"})
        store.append_runtime_event({"goal_id": "g", "event_type": "two"})
        events = self.reopen(store).list_runtime_events("g")
        self.assertEqual([e["event_type"] for e in events], ["one", "two"])

    def test_events_filter_by_goal(self) -> None:
        store = self.make_store()
        store.append_runtime_event({"goal_id": "g1", "event_type": "a"})
        store.append_runtime_event({"goal_id": "g2", "event_type": "b"})
        reopened = self.reopen(store)
        self.assertEqual(len(reopened.list_runtime_events()), 2)
        self.assertEqual([e["event_type"] for e in reopened.list_runtime_events("g1")], ["a"])

    def test_checkpoint_latest_write_wins(self) -> None:
        store = self.make_store()
        store.save_checkpoint("k", {"value": 1})
        store.save_checkpoint("k", {"value": 2})
        self.assertEqual(self.reopen(store).load_checkpoint("k"), {"value": 2})

    def test_nonexistent_lookups_return_none(self) -> None:
        store = self.make_store()
        self.assertIsNone(store.load_goal("missing"))
        self.assertIsNone(store.load_plan("missing"))
        self.assertIsNone(store.load_checkpoint("missing"))

    def test_secret_values_rejected_without_leaking(self) -> None:
        store = self.make_store()
        with self.assertRaises(RuntimeStoreError) as ctx:
            store.save_checkpoint("k", {"token": Secret("must-not-store")})
        self.assertNotIn("must-not-store", str(ctx.exception))
        with self.assertRaises(RuntimeStoreError) as ctx2:
            store.append_runtime_event({"token": "ghp_must_not_store_value"})
        self.assertNotIn("ghp_must_not_store_value", str(ctx2.exception))
