"""Safely load project-local Qwen configuration for direct Python executors.

Next.js loads ``.env.local`` automatically, while standalone Python scripts do
not.  The reconstruction routes must behave identically in both cases.  Values
are never logged and pre-existing process environment variables win.
"""
from __future__ import annotations

import os
from pathlib import Path


def _candidate_roots(anchor: Path | None = None) -> list[Path]:
    roots: list[Path] = []
    for start in (Path.cwd(), anchor.resolve() if anchor else Path(__file__).resolve()):
        current = start if start.is_dir() else start.parent
        while current not in roots:
            roots.append(current)
            if current.parent == current:
                break
            current = current.parent
    return roots


def load_project_env(anchor: Path | None = None) -> Path | None:
    """Load the nearest .env.local, falling back to .env; never override env."""
    for root in _candidate_roots(anchor):
        for filename in (".env.local", ".env"):
            path = root / filename
            if not path.is_file():
                continue
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name = name.strip()
                value = value.strip().strip('"').strip("'")
                if name and value:
                    os.environ.setdefault(name, value)
            return path
    return None
