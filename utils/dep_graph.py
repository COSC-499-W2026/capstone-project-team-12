"""Check and draw the backend's module dependencies.

Usage (from the repo root):
    python utils/dep_graph.py            # print the problems
    python utils/dep_graph.py --check    # also exit 1 if a rule is broken or the graph file is
                                         # out of date (for CI)
    python utils/dep_graph.py --graph    # also redraw docs/architecture/dependency-graph.md

Components follow the architecture diagram and DFDs:
    accounts (1.0), game (2.0), verification (3.0), engagement (4.0), content (5.0)
plus shared code (core: app.config, app.db, app.errors, app.models, app.services) and the
entry layer (app.main, app.api, app.cli).

Rules:
  1. A component may use another component only through its interface module
     (app.game may import app.content.interface, but not app.content.service).
  2. No cycles between components.
  3. Shared code must not import a component or the entry layer.
  4. Components must not import the entry layer.
  5. Every module belongs to a component, core or entry (add new shared modules to CORE).
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "src" / "backend" / "app"
GRAPH_FILE = ROOT / "docs" / "architecture" / "dependency-graph.md"

COMPONENTS = {
    "accounts": "1.0 Auth & Accounts",
    "game": "2.0 Game Session",
    "verification": "3.0 Photo Verification",
    "engagement": "4.0 Class & Engagement",
    "content": "5.0 Content Management",
}
# models: all ORM models (shared schema); services: services used by several components;
# errors: AppError and the error codes every component raises
CORE = {"config", "db", "errors", "models", "services"}
ENTRY = {"main", "api", "cli"}  # HTTP API and command-line jobs

Graph = dict[str, set[str]]


def module_name(path: Path, app_dir: Path) -> str:
    """Turn <app_dir>/game/interface.py into app.game.interface."""
    parts = path.relative_to(app_dir.parent).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def resolve(node: ast.ImportFrom, package: str) -> str:
    """Return the absolute module of a 'from ... import', including relative imports."""
    if not node.level:
        return node.module or ""
    parts = package.split(".")
    base = parts[: len(parts) - node.level + 1]
    return ".".join(base + ([node.module] if node.module else []))


def imports_of(path: Path, module: str) -> set[str]:
    """Return the app.* names that one file imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = resolve(node, package)
            found.add(target)
            # "from app.content import interface" imports a submodule, not a name
            found.update(f"{target}.{alias.name}" for alias in node.names)
    return {name for name in found if name == "app" or name.startswith("app.")}


def known_module(name: str, modules: set[str]) -> str | None:
    """Trim app.db.Base to app.db; return None if no part of the name is a module."""
    while name not in modules and "." in name:
        name = name.rpartition(".")[0]
    return name if name in modules else None


def collect(app_dir: Path) -> tuple[set[str], Graph]:
    """Return every module under app_dir and the import edges between them."""
    files = {module_name(path, app_dir): path for path in sorted(app_dir.rglob("*.py"))}
    modules = set(files)
    edges: Graph = {}
    for module, path in files.items():
        targets = {known_module(name, modules) for name in imports_of(path, module)}
        found = {t for t in targets if t is not None and t != module}
        # "from app.content import interface" lists app.content too; keep only the submodule
        edges[module] = {t for t in found if not any(o.startswith(t + ".") for o in found)}
    return modules, edges


def group(module: str) -> str:
    """Map app.x.y to its component, 'core', 'entry' or 'other'."""
    top = module.split(".")[1] if "." in module else ""
    if top in COMPONENTS:
        return top
    if top in CORE:
        return "core"
    if top in ENTRY:
        return "entry"
    return "other"


def find_cycles(graph: Graph) -> list[list[str]]:
    """Return the cycles found by a depth-first search, e.g. [['a', 'b', 'a']]."""
    cycles: list[list[str]] = []
    state: dict[str, str] = {}
    stack: list[str] = []

    def visit(node: str) -> None:
        state[node] = "open"
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if state.get(nxt) == "open":
                cycles.append(stack[stack.index(nxt) :] + [nxt])
            elif nxt not in state:
                visit(nxt)
        stack.pop()
        state[node] = "done"

    for node in sorted(graph):
        if node not in state:
            visit(node)
    return cycles


def edge_problems(src: str, dst: str) -> list[str]:
    """Return the rule 1, 3 and 4 problems of one import."""
    gs, gd = group(src), group(dst)
    problems = []
    if gs in COMPONENTS and gd in COMPONENTS and gs != gd and dst != f"app.{gd}.interface":
        problems.append(f"rule 1: {src} imports {dst}; use app.{gd}.interface")
    if gs == "core" and gd in {*COMPONENTS, "entry"}:
        problems.append(f"rule 3: shared module {src} imports {dst}")
    if gs in COMPONENTS and gd == "entry":
        problems.append(f"rule 4: {src} imports the entry layer ({dst})")
    return problems


def check(modules: set[str], edges: Graph) -> list[str]:
    """Return every broken rule as a readable message (empty list if all rules pass)."""
    problems: list[str] = []
    components: Graph = defaultdict(set)
    for src in sorted(edges):
        for dst in sorted(edges[src]):
            problems += edge_problems(src, dst)
            if group(src) in COMPONENTS and group(dst) in COMPONENTS and group(src) != group(dst):
                components[group(src)].add(group(dst))
    problems += [
        f"rule 5: {m} is not in a component, CORE or ENTRY"
        for m in sorted(modules)
        if m != "app" and group(m) == "other"
    ]
    problems += ["rule 2: component cycle " + " -> ".join(c) for c in find_cycles(components)]
    return problems


def node_id(module: str) -> str:
    """Mermaid ids cannot contain dots: app.game.service -> app_game_service."""
    return module.replace(".", "_")


def mermaid(modules: set[str], edges: Graph) -> str:
    """Draw the modules as a Mermaid flowchart, one box per component or layer."""
    titles = {
        **COMPONENTS,
        "core": "Shared (config, db, models, services)",
        "entry": "Entry (main, api, cli)",
    }
    lines = ["```mermaid", "flowchart LR"]
    for name in ["entry", *COMPONENTS, "core", "other"]:
        members = sorted(m for m in modules if group(m) == name and m != "app")
        if members:
            lines.append(f'  subgraph {name}["{titles.get(name, "Other")}"]')
            lines += [f'    {node_id(m)}["{m}"]' for m in members]
            lines.append("  end")
    for src in sorted(edges):
        lines += [f"  {node_id(src)} --> {node_id(dst)}" for dst in sorted(edges[src])]
    lines.append("```")
    return "\n".join(lines)


def report(modules: set[str], edges: Graph, problems: list[str]) -> str:
    """Return the Markdown page saved to GRAPH_FILE."""
    n_edges = sum(len(t) for t in edges.values())
    rule_lines = [f"- {p}" for p in problems] or ["All dependency rules pass."]
    return "\n".join(
        [
            "# Backend Dependency Graph",
            "",
            "Generated by `python utils/dep_graph.py --graph`. Do not edit by hand.",
            "",
            f"{len(modules)} modules, {n_edges} import edges.",
            "",
            mermaid(modules, edges),
            "",
            "## Rule check",
            "",
            *rule_lines,
            "",
        ]
    )


def graph_is_up_to_date(graph_file: Path, text: str) -> bool:
    return graph_file.exists() and graph_file.read_text(encoding="utf-8") == text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check and draw the backend's dependencies.")
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if a rule is broken or the graph is stale"
    )
    parser.add_argument("--graph", action="store_true", help="redraw the dependency graph")
    args = parser.parse_args(argv)

    modules, edges = collect(APP_DIR)
    problems = check(modules, edges)
    print(f"{len(modules)} modules, {sum(len(t) for t in edges.values())} import edges")
    print("\n".join(problems) or "All dependency rules pass.")
    text = report(modules, edges, problems)
    if args.graph:
        GRAPH_FILE.parent.mkdir(parents=True, exist_ok=True)
        GRAPH_FILE.write_text(text, encoding="utf-8", newline="\n")  # LF on Windows too
        print(f"wrote {GRAPH_FILE}")
    stale = args.check and not graph_is_up_to_date(GRAPH_FILE, text)
    if stale:
        relative = GRAPH_FILE.relative_to(ROOT) if GRAPH_FILE.is_relative_to(ROOT) else GRAPH_FILE
        print(
            f"{relative} is out of date. Run `python utils/dep_graph.py --graph` "
            "and commit the file with your change."
        )
    return 1 if args.check and (problems or stale) else 0


if __name__ == "__main__":
    sys.exit(main())
