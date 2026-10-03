# Contributing

## Branch Naming

Branch names must follow:

`type/short-description`

Use lowercase words separated by hyphens.

- `feat/` — new feature
- `fix/` — bug fix
- `refactor/` — code change with no behaviour change
- `test/` — tests only
- `docs/` — documentation and weekly logs
- `chore/` — configuration, tooling, and dependencies
- `ci/` — CI workflows
- `perf/` — performance improvements
- `style/` — formatting only
- `hotfix/` — urgent fix on main

Example: `feat/a7-start-game`

## Pull Requests

All changes should be submitted through a pull request.

Before requesting review:

- Make sure your branch is up to date.
- Make sure all CI checks pass.
- Keep the PR focused on one issue or feature.
- Complete the PR template.
- Every PR must include or update tests where applicable.

## Testing

Every PR that changes application behaviour must include appropriate tests.

Backend tests use pytest.

CI automatically checks:

- Ruff linting
- Ruff formatting
- mypy type checking
- Alembic migrations
- pytest
- test coverage

A failing CI check must be fixed before the PR can be merged.