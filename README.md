# Turing Autonomous Agent

A reference design and runnable Python skeleton for a **responsible autonomous agent** inspired by the engineering principles discussed in the Hamming / Simon / Pearl / Engelbart / Brooks roundtable:

- **Hamming**: every action needs redundancy, verification, and recovery.
- **Simon**: the agent operates under bounded rationality and seeks satisficing solutions.
- **Pearl**: actions must be evaluated through causal and counterfactual reasoning, not correlation alone.
- **Engelbart**: the agent should augment human and organizational intelligence, not hide complexity.
- **Brooks**: there is no silver bullet; build narrow, auditable, testable systems with conceptual integrity.

## What this repository contains

```text
turing_agent/
  core.py          # agent architecture and runnable reference implementation
  demo.py          # small command-line demo
  __init__.py

docs/
  ARCHITECTURE.md  # system design
  SAFETY.md        # permission, verification, and human-gating policy

tests/
  test_core.py     # unit tests for core behavior
```

## Core idea

A real autonomous agent is not just an LLM plus tool calls. It is a long-running action system with:

1. goal stack and stopping rules,
2. explicit world/belief model,
3. causal impact estimation,
4. satisficing planner,
5. tool bus with permissions,
6. verification and rollback hooks,
7. human collaboration gates,
8. audit log and memory.

## Quick start

```bash
python -m turing_agent.demo "prepare a safe implementation plan for a research assistant agent"
```

Run tests:

```bash
python -m pytest
```

The package has no required runtime dependencies beyond Python 3.10+.

## Design stance

This project intentionally starts as a narrow, inspectable reference architecture rather than a magical general agent. It is meant to be extended with real LLM clients, real tools, persistent memory, and domain-specific safety policies.

The most important constraint is: **the agent must be able to explain, verify, stop, and recover before it is allowed to act with high impact.**
