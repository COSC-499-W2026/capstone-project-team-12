import json
from pathlib import Path

import export_openapi
import pytest


def test_committed_spec_is_up_to_date() -> None:
    text = export_openapi.render(export_openapi.build_spec())
    assert export_openapi.is_up_to_date(export_openapi.SPEC_FILE, text), (
        "docs/api/openapi.json is out of date: run python utils/export_openapi.py"
    )


def test_export_is_stable() -> None:
    first = export_openapi.render(export_openapi.build_spec())
    assert first == export_openapi.render(export_openapi.build_spec())
    assert first.endswith("}\n")


def test_spec_lists_the_api_routes() -> None:
    paths = export_openapi.build_spec()["paths"]
    assert "/health" in paths
    assert "/api/v1/auth/register" in paths


def test_export_writes_the_file(tmp_path: Path) -> None:
    spec_file = tmp_path / "api" / "openapi.json"
    assert export_openapi.main([], spec_file=spec_file) == 0
    assert json.loads(spec_file.read_text())["openapi"].startswith("3.")
    assert export_openapi.main(["--check"], spec_file=spec_file) == 0


@pytest.mark.parametrize("content", [None, "{}\n"])
def test_check_fails_when_the_file_is_missing_or_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    spec_file = tmp_path / "openapi.json"
    if content is not None:
        spec_file.write_text(content)
    assert export_openapi.main(["--check"], spec_file=spec_file) == 1
    assert "out of date" in capsys.readouterr().out
