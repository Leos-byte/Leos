# Design philosophy

Leos is influenced by:

## Hamming — reliability and verification

Actions should be checked, logged, verified, and recoverable where possible.
Leos turns that principle into transaction phases, output validation,
post-action verification, rollback, recovery packets, and release evidence.

## Simon — bounded rationality and satisficing

Goals should include explicit success criteria, constraints, stop conditions,
and budgets. The runtime favors bounded plans that satisfy declared criteria
over open-ended loops or self-declared completion.

## Pearl — causal reasoning

Tools should state the effects they are expected to cause and the observations
that would support those claims. Leos uses tool-level causal contracts and
post-action observations to detect mismatches. These contracts are partial
runtime enforcement, not a complete structural causal model.

## Engelbart — human augmentation

Consequential automation should improve human control rather than hide it.
Approval packets, audit traces, dry-runs, alternatives, and manual recovery
records keep human reviewers inside the action boundary.

## Brooks — engineered embodied feedback

Reliable behavior comes from concrete interaction with the environment:
bounded tools, observable state, explicit failure modes, small modules, and
tests against real effects. The runtime is designed as an engineered system,
not a single opaque reasoning loop.

These ideas inform Leos, but the project is judged by executable boundaries,
tests, audit evidence, and real smoke results—not by philosophy alone.
