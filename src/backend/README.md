# Backend

FastAPI service (Milestone 1). See the root README for how to run it.

```
src/backend/
├── app/
│   ├── main.py        # app factory, GET /health
│   ├── config.py      # settings from environment variables
│   ├── db.py          # SQLAlchemy engine, session, Base
│   ├── api/v1/        # /api/v1 routers (one per feature area)
│   ├── accounts/      # 1.0 Auth & Accounts (Kaedyn)
│   ├── game/          # 2.0 Game Session (Mia)
│   └── verification/  # 3.0 Photo Verification (Eddy)
├── alembic/           # database migrations
├── crontab            # scheduled jobs for the scheduler container
├── Dockerfile
├── pyproject.toml     # dependency ranges, mypy and pytest settings (ruff: /ruff.toml)
├── requirements.txt   # pinned runtime packages (used by the Docker image)
└── requirements-dev.txt  # runtime + test, lint and type-check tools (local and CI)
```

Tests live in `tests/backend/`.

Each component package has an `interface.py`: the only module other components may import.
Agree on a function signature with its users before adding it there, and check the rules with
`python utils/dep_graph.py --check` from the repo root.

