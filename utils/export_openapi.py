"""Export the backend's OpenAPI document.

Usage (from src/backend, the same as CI; from the repo root drop the "../../"):
    python ../../utils/export_openapi.py          # rewrite docs/api/openapi.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "src" / "backend"
SPEC_FILE = ROOT / "docs" / "api" / "openapi.json"


def build_spec() -> dict[str, Any]:
    """The OpenAPI document of the real app, built the same way FastAPI serves /openapi.json."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))  # so `app` imports from any working directory
    app = importlib.import_module("app.main").create_app()
    spec: dict[str, Any] = app.openapi()
    return spec


def render(spec: dict[str, Any]) -> str:
    """Stable text for the file: same spec in, same bytes out."""
    return json.dumps(spec, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None, spec_file: Path = SPEC_FILE) -> int:
    argparse.ArgumentParser(description="Export the OpenAPI document.").parse_args(argv)

    text = render(build_spec())
    relative = spec_file.relative_to(ROOT) if spec_file.is_relative_to(ROOT) else spec_file
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(text, encoding="utf-8", newline="\n")  # LF on Windows too
    print(f"Wrote {relative}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
