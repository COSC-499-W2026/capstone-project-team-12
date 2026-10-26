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
        ("app.models.users", "core"),
        ("app.services.storage", "core"),
        ("app.api.v1", "entry"),
        ("app.cli", "entry"),
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


def test_components_share_models_and_services(tmp_path: Path) -> None:
    files = {
        "models/users.py": "",
        "services/storage.py": "",
        "game/service.py": "from app.models.users import User\nfrom app.services import storage",
        "engagement/service.py": "from app.models.users import User",
    }
    assert problems_of(tmp_path, files) == []


def test_rule3_models_must_not_import_a_component(tmp_path: Path) -> None:
    files = {"game/interface.py": "", "models/users.py": "import app.game.interface"}
    assert problems_of(tmp_path, files) == [
        "rule 3: shared module app.models.users imports app.game.interface"
    ]


def test_cli_module_counts_as_entry_layer(tmp_path: Path) -> None:
    # crontab runs `python -m app.cli send-emails`, so app.cli must not trip rule 5
    assert problems_of(tmp_path, {"db.py": "", "cli.py": "import app.db"}) == []


def test_check_flag_sets_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(dep_graph, "APP_DIR", make_app(tmp_path, {"helpers.py": ""}))
    assert dep_graph.main([]) == 0
    assert dep_graph.main(["--check"]) == 1
    assert "rule 5: app.helpers" in capsys.readouterr().out


def test_mermaid_draws_groups_and_edges(tmp_path: Path) -> None:
    files = {"content/interface.py": "", "game/service.py": "import app.content.interface"}
    chart = dep_graph.mermaid(*dep_graph.collect(make_app(tmp_path, files)))
    assert chart.startswith("```mermaid\nflowchart LR")
    assert '  subgraph game["2.0 Game Session"]' in chart
    assert '    app_game_service["app.game.service"]' in chart
    assert "  app_game_service --> app_content_interface" in chart
    assert "subgraph accounts" not in chart  # empty groups are left out
    assert 'app["app"]' not in chart  # the root package is not drawn


def test_report_lists_the_rule_check() -> None:
    assert "All dependency rules pass." in dep_graph.report(set(), {}, [])
    assert "- rule 5: app.x is bad" in dep_graph.report(set(), {}, ["rule 5: app.x is bad"])


def test_graph_flag_writes_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph_file = tmp_path / "docs" / "dependency-graph.md"
    monkeypatch.setattr(dep_graph, "APP_DIR", make_app(tmp_path, {"db.py": ""}))
    monkeypatch.setattr(dep_graph, "GRAPH_FILE", graph_file)
    dep_graph.main([])
    assert not graph_file.exists()
    assert dep_graph.main(["--graph"]) == 0
    assert '    app_db["app.db"]' in graph_file.read_text()


def use_fake_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> Path:
    """Point the script at a fake app and a graph file in tmp_path; return that file."""
    graph_file = tmp_path / "docs" / "dependency-graph.md"
    monkeypatch.setattr(dep_graph, "APP_DIR", make_app(tmp_path, files))
    monkeypatch.setattr(dep_graph, "GRAPH_FILE", graph_file)
    return graph_file


def test_check_passes_when_the_graph_was_just_drawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_app(tmp_path, monkeypatch, {"db.py": ""})
    assert dep_graph.main(["--graph"]) == 0
    assert dep_graph.main(["--check"]) == 0


def test_check_fails_when_the_graph_file_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    use_fake_app(tmp_path, monkeypatch, {"db.py": ""})
    assert dep_graph.main(["--check"]) == 1
    assert "is out of date. Run `python utils/dep_graph.py --graph`" in capsys.readouterr().out


def test_check_fails_when_a_module_was_added_after_the_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_app(tmp_path, monkeypatch, {"db.py": ""})
    assert dep_graph.main(["--graph"]) == 0
    (dep_graph.APP_DIR / "errors.py").write_text("import app.db")
    assert dep_graph.main(["--check"]) == 1
    assert dep_graph.main(["--graph"]) == 0
    assert dep_graph.main(["--check"]) == 0


def test_graph_check_is_not_needed_without_the_check_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_fake_app(tmp_path, monkeypatch, {"db.py": ""})
    # no graph file, but nothing asked for a check
    assert dep_graph.main([]) == 0
