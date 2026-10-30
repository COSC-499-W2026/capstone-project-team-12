import json
import re
from pathlib import Path
from typing import Any

import export_openapi

from app.errors import AppError, ErrorCode, ErrorResponse

CAMEL_CASE = re.compile(r"[a-z]+(?:[A-Z][a-z0-9]*)+")
# a path item can also hold "parameters", "summary" and so on, which are not operations
METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def operations(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [op for path in spec["paths"].values() for m, op in path.items() if m in METHODS]


def test_operations_skip_path_level_fields() -> None:
    path = {"parameters": [{"name": "id", "in": "path"}], "get": {"operationId": "getGame"}}
    assert operations({"paths": {"/games/{id}": path}}) == [{"operationId": "getGame"}]


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


def test_operation_ids_are_explicit_camel_case_and_unique() -> None:
    ids = [op["operationId"] for op in operations(export_openapi.build_spec())]
    assert all(CAMEL_CASE.fullmatch(op_id) for op_id in ids), ids  # api-conventions.md
    assert len(ids) == len(set(ids))


def test_errors_use_our_format_not_fastapis_default() -> None:
    spec = export_openapi.build_spec()
    schemas = spec["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    for op in operations(spec):
        for status, response in op["responses"].items():
            if not status.startswith("2"):
                schema = response["content"]["application/json"]["schema"]
                assert schema == {"$ref": "#/components/schemas/ErrorResponse"}


def test_register_lists_its_error_statuses() -> None:
    register = export_openapi.build_spec()["paths"]["/api/v1/auth/register"]["post"]
    assert {"201", "409", "422"} <= set(register["responses"])


def test_game_actions_declare_a_conflict() -> None:
    by_id = {op["operationId"]: op for op in operations(export_openapi.build_spec())}
    for operation_id in ("pauseGame", "resumeGame", "quitGame", "resetGame"):
        assert "409" in by_id[operation_id]["responses"]


def test_error_response_model_matches_app_error_body() -> None:
    body = AppError(ErrorCode.EMAIL_TAKEN, "Email in use", [{"field": "email"}]).body()
    assert ErrorResponse.model_validate(body).model_dump(mode="json") == json.loads(
        json.dumps(body)
    )
