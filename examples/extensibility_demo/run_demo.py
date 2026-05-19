from __future__ import annotations

from pathlib import Path

from leos_agent import EchoTool, Goal
from leos_agent.credentials import InMemoryCredentialVault
from leos_agent.evaluator_registry import EvaluatorRegistry
from leos_agent.goals import GoalProgress
from leos_agent.runtime_store import InMemoryRuntimeStore
from leos_agent.state import WorldState
from leos_agent.tool_manifest_registry import ToolManifestRegistry
from leos_agent.tools import Secret


def main() -> int:
    root = Path(__file__).parent
    registry = ToolManifestRegistry()
    registry.load_directory(root / "manifests")
    registry.validate_against_tool(EchoTool())
    print(f"manifest loaded: {registry.names()[0]}")

    goal = Goal("Run an extensibility demo", ["do the task"], stop_conditions=["done"])
    evaluation = EvaluatorRegistry().evaluate(goal, WorldState(), GoalProgress(total_steps=1, verified_steps=1))
    print(f"evaluator result: {evaluation.status.value}")

    store = InMemoryRuntimeStore()
    store.save_goal(goal)
    store.save_checkpoint("extensibility_demo:final", {"goal_id": goal.goal_id, "status": evaluation.status.value})
    print("checkpoint saved")

    vault = InMemoryCredentialVault()
    handle = vault.put(Secret("demo-secret-value"), scope="demo")
    print(f"secret handle created: {handle.handle_id}")
    print("secret not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
