# Team 12

## Project Structure

```
.
├── docs                    # Documentation files
│   ├── contract            # Team contract
│   ├── proposal            # Project proposal
│   ├── design               # UI mocks
│   ├── minutes              # Minutes from team meetings
│   └── ...
├── logs                     # Weekly logs: logs/<StudentName>/week-K.md, logs/team/week-K.md
├── src                      # Source files
│   ├── backend              # FastAPI service (M1)
│   └── frontend             # React + TypeScript web app (M2)
├── tests                    # Automated tests
│   ├── backend              # pytest
│   ├── frontend             # Vitest component tests (M2)
│   └── e2e                  # Playwright end-to-end tests (M2)
├── utils                    # Utility scripts (e.g., GPS replay)
└── README.md
```

## Running Locally

Requirements: Docker Desktop (Mac or Windows) or Docker Engine (Linux).

```bash
cp .env.example .env
docker compose up --build
```

| Service | URL | Purpose |
|---|---|---|
| api | http://localhost:8000/health, http://localhost:8000/docs | FastAPI backend and its OpenAPI docs |
| db | localhost:5432 | PostgreSQL 16 |
| mailpit | http://localhost:8025 | Catches emails sent in development |
| scheduler | (no port) | Runs scheduled jobs with supercronic |

Database migrations run automatically when the api container starts.

## Backend Development

The backend uses Python 3.11 in a virtual environment (venv).

```bash
cd src/backend
python3.11 -m venv .venv
source .venv/bin/activate            # Windows: see below
pip install -r requirements-dev.txt  # pinned runtime and development packages

pytest                               # tests (in tests/backend)
ruff check . ../../tests ../../utils   # lint
ruff format . ../../tests ../../utils  # format
mypy                                 # type check (strict)
python ../../utils/dep_graph.py      # dependency rules between components
alembic revision --autogenerate -m "describe change"   # new migration
```

To run the API from the venv with live reload (database and Mailpit still in Docker):

```bash
docker compose up -d db mailpit
alembic upgrade head
uvicorn app.main:app --reload        # http://localhost:8000/docs
```

On Windows, use these steps to create and activate the venv. The other commands are the same, and paths like `../../tests/backend` work with forward slashes on Windows too.

- Create: `py -3.11 -m venv .venv`
- Activate in cmd: `.venv\Scripts\activate.bat`
- Activate in PowerShell: `.venv\Scripts\Activate.ps1` (if PowerShell blocks the script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once)

Adding a package: add it to `dependencies` (or `dev`) in `pyproject.toml`, install it with `pip install`,
then add its exact version (`pip freeze`) to `requirements.txt` or `requirements-dev.txt` in the same commit.
Never commit `.venv/`.

## Team Contract

Our team contract can be found [here](docs/contract/team-contract.docx).

## Project Proposal

Our project proposal can be found [here](docs/proposal/project-proposal.docx)
