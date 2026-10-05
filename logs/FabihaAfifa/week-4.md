# Week 4 Individual Log
## Fabiha Afifa

As part of Issue #7, the team needs CI and testing infrastructure to automatically verify backend contributions before they are merged.

Therefore, I added a GitHub Actions CI workflow for pull requests with Ruff linting and formatting checks, strict mypy type checking for `app/`, a fresh PostgreSQL service with an Alembic migration check, pytest coverage reporting, and a test database fixture with per-test transaction rollback. I also added `pytest-cov` as a development dependency and `CONTRIBUTING.md` with the team's branch naming, pull request, and testing guidelines.

When I reviewed the CI and testing setup, I verified each configured check locally. Ruff linting and formatting passed, mypy passed, all 6 pytest tests passed, and the coverage report generated successfully.

The PR does not contain a temporary workaround for the planned `make_user` fixture. I intentionally did not add this fixture because the project does not currently have a `User` class/model to instantiate. It can be implemented once the User model is available.

I checked the CI and testing changes for unnecessary duplication and unrelated responsibilities by keeping CI configuration, test infrastructure, development dependencies, and contribution documentation in their appropriate files. This keeps the infrastructure reusable for future backend contributions.

This work belongs in the backend development and testing infrastructure because it verifies code quality, database migrations, and automated tests before backend changes are merged.

### Testing Receipts for PR #128

The CI and testing infrastructure works correctly because I locally verified the same checks configured for CI: Ruff check passed, Ruff format check passed, strict mypy type checking passed, all 6 pytest tests passed, and the coverage report generated successfully.

The database fixture provides test isolation by wrapping each test in a transaction and rolling it back afterward. The `make_user` fixture could not yet be implemented or tested because the required `User` model does not currently exist.

The backend tests and shared test infrastructure are located under `tests/backend/`.

I confirmed that the new infrastructure did not break the existing backend tests because all 6 pytest tests passed after the changes.