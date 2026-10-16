# Contributing

## Branch Naming

Branch names must follow:

`type/short-description`

Use lowercase words separated by hyphens.

- `feat/` — new feature
- `fix/` — bug fix
- `refactor/` — code change with no behaviour change
- `test/` — tests only
- `docs/` — documentation
- `chore/` — configuration, tooling, and dependencies
- `ci/` — CI workflows
- `perf/` — performance improvements
- `style/` — formatting only
- `hotfix/` — urgent fix on main

Example: `feat/a7-start-game`

Weekly logs only live on the `weekly-logs` branch. Branch from `weekly-logs` and open the PR into `weekly-logs`. Never merge `weekly-logs` into `main`, or the logs come back to `main`.

## Pull Requests

All changes should be submitted through a pull request.

Before requesting review:

- Make sure your branch is up to date.
- Make sure all CI checks pass.
- Keep the PR focused on one issue or feature.
- Include **What**, **Why**, **How to verify**, and **Size** in the PR description.
- Every PR must include or update tests where applicable.

Pull requests require **2 approvals** before merging. Weekly log pull requests require **1 approval**.

When merging:

- Use **Create a merge commit** only.
- Do not delete the branch after merging.

## Testing

Every PR that changes application behaviour must include appropriate tests.

Backend tests use pytest.

CI automatically checks:

- Ruff linting
- Ruff formatting
- mypy type checking
- Alembic migrations
- the committed OpenAPI document (`docs/api/openapi.json`) matches the code
- pytest
- test coverage

## API changes

`docs/api/openapi.json` is generated from the FastAPI routes, so reviewers can see every API change in the PR diff. If your PR adds or changes a route, a request or response model, or an error response, run this from `src/backend` and commit the updated file with your change:

```
pip install -r requirements-dev.txt   # pinned versions; use Python 3.11 like CI (3.13 names 422 differently)
python ../../utils/export_openapi.py
```

CI fails with "out of date" if you forget. When you add a route:

- **Required:** set `operation_id` in camelCase, following the Operation IDs table in `docs/api-conventions.md` (for example `operation_id="pauseGame"`). FastAPI's default names don't follow it, and a test fails if one is missing.
- **Recommended:** if the route can return other errors (e.g. 404, 409), list them with `responses=error_responses(...)` from `app.errors`, for example `error_responses(HTTPStatus.CONFLICT)`, so they show up in the spec. Errors raised as `AppError` in a service are invisible to FastAPI otherwise. Reviewers can point out missing ones, but it isn't a blocker. 422 is already added for every route under `/api/v1`.

A failing CI check must be fixed before the PR can be merged.
