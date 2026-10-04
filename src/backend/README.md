# Backend

FastAPI service (Milestone 1). See the root README for how to run it.

```
src/backend/
├── app/
│   ├── main.py        # app factory, GET /health
│   ├── config.py      # settings from environment variables
│   ├── db.py          # SQLAlchemy engine, session, Base
│   └── api/v1/        # /api/v1 routers (one per feature area)
├── alembic/           # database migrations
├── crontab            # scheduled jobs for the scheduler container
├── Dockerfile
├── pyproject.toml     # dependency ranges, mypy and pytest settings (ruff: /ruff.toml)
├── requirements.txt   # pinned runtime packages (used by the Docker image)
└── requirements-dev.txt  # runtime + test, lint and type-check tools (local and CI)
```

Tests live in `tests/backend/`.

