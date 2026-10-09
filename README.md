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

Weekly logs are not on `main`. They live on the [`weekly-logs`](https://github.com/COSC-499-W2026/capstone-project-team-12/tree/weekly-logs) branch, as `logs/<StudentName>/week-K.md` and `logs/team/week-K.md`.

## System Design

All diagrams are drawn in draw.io. The source files are in our shared Google Drive folder, [DFD & System Architecture](https://drive.google.com/drive/folders/1nHJDhfVOvWCLNEbqzlOMssgTrlTugm7J?usp=sharing).

### System Architecture

[System architecture diagram](https://drive.google.com/file/d/1frQja1HkuoyBKi2M8U8e4mutHEq50EL-/view?usp=sharing)

Milestone 1 is the backend only. API consumers (Swagger UI, a Postman collection, and GPS replay and photo-set simulation scripts) call the FastAPI backend over HTTPS with a JWT. In Milestone 2 the React web app (student app, teacher portal and admin panel) is added behind nginx.

Inside the backend container, the routers under `/api/v1` handle requests and pass them to five components. The components share a core (`config`, `db`, `models` and shared `services` such as storage, email and audio), and they only call each other through each component's `interface.py`, which `utils/dep_graph.py --check` enforces in CI. Data lives in PostgreSQL 16 (stores D1 to D7) and a media volume (D8) for clue audio, feedback audio and photos. A scheduler container runs the CLI jobs (send emails, expire idle sessions, purge old photos) and emails go out through an SMTP provider.

Each major component has its own Level 1 DFD. In the architecture diagram, clicking a component box opens it.

| Component | Code package | Owner | What it does | DFD Level 1 |
|---|---|---|---|---|
| 1.0 Auth & Accounts | `app/accounts` | Kaedyn | Sign-up, login and refresh tokens, email verification and password reset, profile, staff accounts and admin support, teaching assistant management, privacy export and anonymisation | [1.0 Auth & Accounts](https://drive.google.com/file/d/1BkW_Tt_Tdvax9GVgqysyoxHLFLn8zJqU/view?usp=sharing) |
| 2.0 Game Session | `app/game` | Mia | Starts a session and draws 5 locations, serves clues, processes GPS pings and gives closer/farther feedback, confirms arrival, pause/resume/quit/reset/shuffle, finishes locations, game history, expires idle sessions | [2.0 Game Session](https://drive.google.com/file/d/1So7fPBw_lazmH7Ljxl3l1mUTpDaYw8CK/view?usp=sharing) |
| 3.0 Photo Verification | `app/verification` | Eddy | Checks and re-encodes photo uploads, reads the room number with OCR, applies the decision table (match, unclear, no match), checks photo integrity, purges old photos | [3.0 Photo Verification](https://drive.google.com/file/d/1kY18-D2VZEjEweC--rPVKNOs8rY9V-gK/view?usp=sharing) |
| 4.0 Class & Engagement | `app/engagement` | Fabiha | Classes, access codes, joining and leaving, archiving; points, streaks, daily challenges, achievements, leaderboard, analytics and participation export | [4.0 Class & Engagement](https://drive.google.com/file/d/1Jv7HTKFsB6iuQeSFVExm_WjkX3N_WX2O/view?usp=sharing) |
| 5.0 Content Management | `app/content` | Eddy | Locations and entrance points, clue text and audio upload, bulk imports, audio problem reports, coverage report | [5.0 Content Management](https://drive.google.com/file/d/10yTQwegGvxVM4gUaT9O-LkpPwKqH-z8l/view?usp=sharing) |

### Data Flow Diagrams

- [Level 0 (context diagram)](https://drive.google.com/file/d/11wntSjPr60qeLwDX-lv27QMxHI4oW7eI/view?usp=sharing): the whole system as one process, with its six external entities: Student, Teacher / TA, Admin, Content Manager, Email System and Scheduler.
- Level 1: one diagram per architecture component, linked in the table above. Each one breaks its component into numbered processes (for example 2.1 Start session to 2.8 Expire idle sessions).

How to read the Level 1 diagrams:
- Data stores use the same numbers in every diagram: D1 Users & Auth, D2 Email Outbox, D3 Classes, D4 Content, D5 Game Sessions, D6 Photo Attempts, D7 Points & Engagement and D8 Media Storage. A store marked with `*` is drawn again on the same diagram.
- Dashed circles labelled "Other DFD" show data that goes to or comes from another component. For example, 2.0 sends each location result to 4.0, which sends back the updated points, streak and badges.

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

### Module dependencies

Feature code goes in one package per component from the architecture diagram: `accounts` (1.0),
`game` (2.0), `verification` (3.0), `engagement` (4.0) and `content` (5.0). Shared code sits next
to them: `config`, `db`, `models` (all ORM models, since the schema is shared) and `services`
(services used by several components, such as storage and email). So does the entry layer (`main`,
`api`, and `cli` for the scheduled jobs). A component may use another component only through its
`interface.py`; when two components need to notify each other, the router or CLI command calls both.

```bash
python utils/dep_graph.py --check   # fail if a dependency rule is broken
python utils/dep_graph.py --graph   # redraw docs/architecture/dependency-graph.md
```

The current graph is in [docs/architecture/dependency-graph.md](docs/architecture/dependency-graph.md).

## Team Contract

Our team contract can be found [here](docs/contract/team-contract.docx).

## Project Proposal

Our project proposal can be found [here](docs/proposal/project-proposal.docx)
