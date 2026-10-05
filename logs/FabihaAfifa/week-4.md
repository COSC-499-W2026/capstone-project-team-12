# Week 4 Individual Log
## Fabiha Afifa

Week 4 Individual Log (Sep 28 – Oct 4):
Tasks Completed:
This week, I focused on setting up the backend CI pipeline and test infrastructure in preparation for Milestone 1 development.

Set up a GitHub Actions CI workflow to run automatically on pull requests.
Configured CI checks for Ruff linting and formatting, strict mypy type checking, Alembic migrations against a fresh PostgreSQL service, and pytest with a coverage report.
Added pytest-cov to the backend development dependencies.
Expanded the shared backend test setup with a db fixture that uses a transaction for each test and rolls it back afterward.
Verified the existing /health endpoint test runs successfully with the test suite.
Added CONTRIBUTING.md documenting the team's branch naming convention, pull request expectations, and testing requirements.
Locally verified Ruff, formatting, mypy, pytest, and coverage checks.
The planned make_user test fixture could not yet be implemented because the backend does not currently have a User class/model. This will need to be added once the User model is available.

Issues and Pull Requests
CI pipeline and test setup — Issue #[7]
CI and test infrastructure — PR #[128]
Reflection
The main challenge this week was that the CI work depended on repository structure, Python tooling, database/Alembic setup, Docker configuration, and the health endpoint that were being developed in other branches. I based my CI work on the latest approved setup branch so I could continue without waiting for all prerequisite PRs to be merged into main.

By the end of the week, the backend CI and test infrastructure was set up so that future pull requests can automatically be checked for linting, formatting, type errors, migration problems, and failing tests before being merged.
