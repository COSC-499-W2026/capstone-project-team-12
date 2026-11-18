"""A4 (#71) Test Case 3: no response a student can get names a field that gives a location away.

Student-facing: every /api/v1 route without a role guard that refuses students. That includes
the /teacher class routes, since a TA can be a student account.
"""

import importlib
import pkgutil
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute

import app.api.v1
from app.accounts.interface import can_manage_content, require_admin, require_staff
from app.main import create_app

API_PREFIX = "/api/v1"
ROLE_GUARDS: set[Callable[..., Any]] = {require_staff, can_manage_content, require_admin}

# Distance counts too: a few distances from known spots give the target away. Clue text counts
# because clues are only played, never shown.
FORBIDDEN_WORDS = {
    "room",
    "answer",
    "answers",
    "accepted",
    "ocr",
    "text",
    "transcript",
    "lat",
    "lng",
    "latitude",
    "longitude",
    "coordinate",
    "coordinates",
    "entrance",
    "entrances",
    "distance",
    "radius",
    "building",
    "floor",
}
# "name" alone is a class or badge name, and "gps" alone is in gps_override_available_at
FORBIDDEN_NAMES = {"location_name", "gps_points", "gps_points_snapshot"}


def gives_location_away(field: str) -> bool:
    name = field.lower()
    return name in FORBIDDEN_NAMES or not FORBIDDEN_WORDS.isdisjoint(name.split("_"))


def api_routes() -> list[APIRoute]:
    """The routes of every router in app.api.v1."""
    routers: dict[int, APIRouter] = {}
    for module in pkgutil.iter_modules(app.api.v1.__path__):
        for value in vars(importlib.import_module(f"app.api.v1.{module.name}")).values():
            if isinstance(value, APIRouter):
                routers[id(value)] = value
    return [
        route
        for router in routers.values()
        for route in router.routes
        if isinstance(route, APIRoute)
    ]


def operation_keys(route: APIRoute) -> Iterator[tuple[str, str]]:
    for method in route.methods:
        yield method, API_PREFIX + route.path_format


def is_guarded(route: APIRoute) -> bool:
    pending = [route.dependant]
    while pending:
        dependant = pending.pop()
        if dependant.call in ROLE_GUARDS:
            return True
        pending.extend(dependant.dependencies)
    return False


def field_names(schema: Any, components: dict[str, Any], seen: set[str]) -> Iterator[str]:
    """Every property name in a JSON schema, following $ref, items, maps and unions."""
    if not isinstance(schema, dict):
        return
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name not in seen:
            seen.add(name)
            yield from field_names(components[name], components, seen)
        return
    for field, subschema in schema.get("properties", {}).items():
        yield field
        yield from field_names(subschema, components, seen)
    for key in ("items", "additionalProperties"):
        yield from field_names(schema.get(key), components, seen)
    for key in ("allOf", "anyOf", "oneOf"):
        for subschema in schema.get(key, []):
            yield from field_names(subschema, components, seen)


def response_fields(operation: dict[str, Any], components: dict[str, Any]) -> set[str]:
    fields: set[str] = set()
    for response in operation.get("responses", {}).values():
        for media in response.get("content", {}).values():
            fields |= set(field_names(media.get("schema"), components, set()))
    return fields


@pytest.fixture(scope="module")
def openapi() -> dict[str, Any]:
    return create_app().openapi()


@pytest.fixture(scope="module")
def student_operations(openapi: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (method, path): openapi["paths"][path][method.lower()]
        for route in api_routes()
        if not is_guarded(route)
        for method, path in operation_keys(route)
    }


def test_no_student_response_names_a_location_field(
    openapi: dict[str, Any], student_operations: dict[tuple[str, str], dict[str, Any]]
) -> None:
    components = openapi["components"]["schemas"]
    leaks = sorted(
        f"{method} {path}: {field}"
        for (method, path), operation in student_operations.items()
        for field in response_fields(operation, components)
        if gives_location_away(field)
    )

    assert not leaks, "\n".join(leaks)


def test_every_api_route_is_scanned(openapi: dict[str, Any]) -> None:
    scanned = {key for route in api_routes() for key in operation_keys(route)}
    documented = {
        (method.upper(), path)
        for path, item in openapi["paths"].items()
        if path.startswith(API_PREFIX + "/")
        for method in item
    }

    assert scanned == documented


def test_only_role_guarded_routes_are_skipped(
    student_operations: dict[tuple[str, str], dict[str, Any]],
) -> None:
    assert {
        ("GET", "/api/v1/games/{game_id}/current-location"),
        ("POST", "/api/v1/games/{game_id}/navigation/ping"),
        ("POST", "/api/v1/games/{game_id}/navigation/confirm-arrival"),
        ("GET", "/api/v1/games/{game_id}/summary"),
        ("GET", "/api/v1/me/games"),
        ("POST", "/api/v1/teacher/classes/{class_id}/access-codes"),
    } <= set(student_operations)
    assert ("GET", "/api/v1/content/locations") not in student_operations
    assert ("GET", "/api/v1/content/coverage") not in student_operations


@pytest.mark.parametrize(
    "field",
    [
        "room_number",
        "Room_Number",
        "expected_room",
        "accepted_answers",
        "ocr_text",
        "text",
        "target_lng",
        "latitude",
        "entrances",
        "gps_points",
        "radius_snapshot",
        "distance_m",
        "building_name",
        "floor_number",
        "location_name",
    ],
)
def test_a_location_field_is_caught(field: str) -> None:
    assert gives_location_away(field)


@pytest.mark.parametrize(
    "field",
    ["name", "location_id", "locations", "gps_override_available_at", "points_awarded", "trend"],
)
def test_other_fields_are_not(field: str) -> None:
    assert not gives_location_away(field)


def test_nested_and_referenced_fields_are_found() -> None:
    components = {
        "Inner": {"properties": {"room_number": {"type": "string"}}},
        "Outer": {
            "properties": {
                "items": {"type": "array", "items": {"$ref": "#/components/schemas/Inner"}},
                "extra": {"anyOf": [{"$ref": "#/components/schemas/Inner"}, {"type": "null"}]},
                "by_key": {"additionalProperties": {"properties": {"lat": {"type": "number"}}}},
            }
        },
    }
    schema = {"$ref": "#/components/schemas/Outer"}
    operation = {"responses": {"200": {"content": {"application/json": {"schema": schema}}}}}

    assert response_fields(operation, components) == {
        "items",
        "extra",
        "by_key",
        "room_number",
        "lat",
    }
