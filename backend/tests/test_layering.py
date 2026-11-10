"""Layered-architecture contract tests.

Regression guards for the src/{api,services,core,data} restructure:

1. The mandated layout exists (and the legacy ``backend/app`` package is gone).
2. No module still imports the pre-refactor ``app.*`` package.
3. ``src/core`` stays a leaf layer: it never imports api/services/data.

These are filesystem/AST checks only - no database or network required.
"""

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SRC = BACKEND / "src"

# Folders whose python files are scanned for legacy imports.
SCANNED_ROOTS = ("src", "tests", "alembic", "scripts")


def _imports_of(path: Path):
    """Yield the top-level absolute modules imported by a python file."""
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                yield node.module


def _py_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def test_mandated_layout_exists():
    """The instructor-mandated folders/files exist; `app/` is gone."""
    required = [
        SRC / "api",
        SRC / "services",
        SRC / "core",
        SRC / "data",
        BACKEND / "tests",
        BACKEND / "models",
        BACKEND / "Dockerfile",
        BACKEND / "requirements.txt",
    ]
    for path in required:
        assert path.exists(), f"missing mandated path: {path}"
    assert not (BACKEND / "app").exists(), "legacy backend/app package still exists"


def test_no_legacy_app_imports():
    """No source/test/migration file imports the pre-refactor `app` package."""
    offenders = []
    for root_name in SCANNED_ROOTS:
        root = BACKEND / root_name
        if not root.exists():
            continue
        for path in _py_files(root):
            for mod in _imports_of(path):
                if mod == "app" or mod.startswith("app."):
                    offenders.append(f"{path.relative_to(BACKEND)}: imports {mod}")
    assert not offenders, "legacy `app` imports found:\n" + "\n".join(offenders)


def test_core_layer_imports_nothing_upward():
    """src/core must not depend on the api, services or data layers."""
    offenders = []
    for path in _py_files(SRC / "core"):
        for mod in _imports_of(path):
            if mod.startswith(("src.api", "src.services", "src.data")):
                offenders.append(f"{path.relative_to(BACKEND)}: imports {mod}")
    assert not offenders, "src/core has upward imports:\n" + "\n".join(offenders)
