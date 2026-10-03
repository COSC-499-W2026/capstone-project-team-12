from pathlib import Path

import dep_graph
import pytest


def make_app(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a fake app/ package, e.g. {"game/service.py": "import app.db"}."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "__init__.py").write_text("")
    for name, source in files.items():
        path = app_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return app_dir


def problems_of(tmp_path: Path, files: dict[str, str]) -> list[str]:
    return dep_graph.check(*dep_graph.collect(make_app(tmp_path, files)))


def test_real_backend_follows_the_rules() -> None:
    assert dep_graph.check(*dep_graph.collect(dep_graph.APP_DIR)) == []


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ("app.game.service", "game"),
        ("app.content.interface", "content"),
        ("app.db", "core"),
        ("app.api.v1", "entry"),
        ("app.helpers", "other"),
    ],
)
def test_group(module: str, expected: str) -> None:
    assert dep_graph.group(module) == expected


def test_relative_import_is_resolved(tmp_path: Path) -> None:
    app_dir = make_app(tmp_path, {"game/models.py": "", "game/service.py": "from .models import X"})
    _, edges = dep_graph.collect(app_dir)
    assert edges["app.game.service"] == {"app.game.models"}


def test_imported_name_is_trimmed_to_its_module(tmp_path: Path) -> None:
    app_dir = make_app(tmp_path, {"db.py": "", "game/service.py": "from app.db import Base"})
    _, edges = dep_graph.collect(app_dir)
    assert edges["app.game.service"] == {"app.db"}


def test_submodule_import_keeps_only_the_submodule(tmp_path: Path) -> None:
    files = {"content/interface.py": "", "game/service.py": "from app.content import interface"}
    _, edges = dep_graph.collect(make_app(tmp_path, files))
    assert edges["app.game.service"] == {"app.content.interface"}


def test_import_through_interface_is_allowed(tmp_path: Path) -> None:
    files = {"content/interface.py": "", "game/service.py": "from app.content.interface import f"}
    assert problems_of(tmp_path, files) == []


def test_rule1_import_of_component_internals(tmp_path: Path) -> None:
    files = {"content/service.py": "", "game/service.py": "from app.content.service import f"}
    assert problems_of(tmp_path, files) == [
        "rule 1: app.game.service imports app.content.service; use app.content.interface"
    ]


def test_rule2_cycle_between_components(tmp_path: Path) -> None:
    files = {
        "game/interface.py": "import app.content.interface",
        "content/interface.py": "import app.game.interface",
    }
    assert problems_of(tmp_path, files) == ["rule 2: component cycle content -> game -> content"]


def test_rule3_shared_code_imports_a_component(tmp_path: Path) -> None:
    files = {"game/interface.py": "", "db.py": "import app.game.interface"}
    assert problems_of(tmp_path, files) == [
        "rule 3: shared module app.db imports app.game.interface"
    ]


def test_rule4_component_imports_the_entry_layer(tmp_path: Path) -> None:
    files = {"main.py": "", "game/service.py": "from app.main import app"}
    assert problems_of(tmp_path, files) == [
        "rule 4: app.game.service imports the entry layer (app.main)"
    ]


def test_rule5_module_outside_any_group(tmp_path: Path) -> None:
    assert problems_of(tmp_path, {"helpers.py": ""}) == [
        "rule 5: app.helpers is not in a component, CORE or ENTRY"
    ]


def test_check_flag_sets_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(dep_graph, "APP_DIR", make_app(tmp_path, {"helpers.py": ""}))
    assert dep_graph.main([]) == 0
    assert dep_graph.main(["--check"]) == 1
    assert "rule 5: app.helpers" in capsys.readouterr().out
