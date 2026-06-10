#!/usr/bin/env python3
"""Build and install the beta wheel in an isolated virtual environment."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="leos-package-check-") as tmp:
        root = Path(tmp)
        dist = root / "dist"
        environment = root / "venv"
        _run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(dist)], cwd=ROOT)
        wheels = sorted(dist.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("package build did not produce exactly one wheel")
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        leos = environment / ("Scripts/leos.exe" if sys.platform == "win32" else "bin/leos")
        _run([str(python), "-m", "pip", "install", str(wheels[0])], cwd=ROOT)
        _run([str(leos), "--help"], cwd=ROOT)
        _run([str(leos), "doctor", "--profile", "production_github_only"], cwd=ROOT)
    print("package_verification_status=passed")
    return 0


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, shell=False)  # nosec B603


if __name__ == "__main__":
    raise SystemExit(main())
