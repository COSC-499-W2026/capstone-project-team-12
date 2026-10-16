"""Export the backend's OpenAPI document and check that the committed copy is up to date.

Usage (from src/backend, the same as CI; from the repo root drop the "../../"):
    python ../../utils/export_openapi.py          # rewrite docs/api/openapi.json
    python ../../utils/export_openapi.py --check  # exit 1 if docs/api/openapi.json is out of date
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


def is_up_to_date(spec_file: Path, text: str) -> bool:
    return spec_file.exists() and spec_file.read_text(encoding="utf-8") == text


def main(argv: list[str] | None = None, spec_file: Path = SPEC_FILE) -> int:
    parser = argparse.ArgumentParser(description="Export or check the OpenAPI document.")
    parser.add_argument("--check", action="store_true", help="exit 1 if the file is out of date")
    args = parser.parse_args(argv)

    text = render(build_spec())
    relative = spec_file.relative_to(ROOT) if spec_file.is_relative_to(ROOT) else spec_file
    if args.check:
        if is_up_to_date(spec_file, text):
            print(f"{relative} is up to date.")
            return 0
        print(
            f"{relative} is out of date. From src/backend run "
            "`python ../../utils/export_openapi.py` and commit the file with your change."
        )
        return 1
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(text, encoding="utf-8", newline="\n")  # LF on Windows too
    print(f"Wrote {relative}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
