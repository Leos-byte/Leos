.PHONY: test safety bench lint format-check type coverage security mutation-smoke fuzz-smoke proof-light proof-check production-readiness check

test:
	PYTHONPATH=src python -m unittest discover -s tests

safety:
	PYTHONPATH=src python -m leos_agent.cli eval --suite safety

bench:
	PYTHONPATH=src:. python -m benchmarks.runner

lint:
	ruff check .

format-check:
	ruff format --check .

type:
	mypy src

coverage:
	PYTHONPATH=src coverage run -m unittest discover -s tests
	coverage report --fail-under=83

security:
	bandit -r src

mutation-smoke:
	python scripts/mutation_smoke.py

fuzz-smoke:
	PYTHONPATH=src python scripts/fuzz_smoke.py

proof-light:
	PYTHONPATH=src python scripts/generate_proofs.py --output docs/proofs --allow-dirty --no-run

proof-check:
	python scripts/check_release_proof.py

production-readiness:
	python scripts/check_production_readiness.py --profile production_github_only

check:
	ruff check .
	ruff format --check .
	mypy src
	PYTHONPATH=src coverage run -m unittest discover -s tests
	coverage report --fail-under=83
	bandit -r src
	PYTHONPATH=src python -m leos_agent.cli eval --suite safety
	PYTHONPATH=src:. python -m benchmarks.runner
