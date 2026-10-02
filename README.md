# Team 12

## Project Structure

```
.
├── docs                    # Documentation files
│   ├── contract            # Team contract
│   ├── proposal            # Project proposal
│   ├── design               # UI mocks
│   ├── minutes              # Minutes from team meetings
│   ├── logs                 # Team and individual Logs
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

## Backend Development

The backend uses Python 3.11 in a virtual environment (venv).

```bash
cd src/backend
python3.11 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt  # pinned runtime and development packages

pytest                               # tests (in tests/backend)
ruff check . ../../tests/backend     # lint
ruff format . ../../tests/backend    # format
mypy                                 # type check (strict)
```

Adding a package: add it to `dependencies` (or `dev`) in `pyproject.toml`, install it with `pip install`,
then add its exact version (`pip freeze`) to `requirements.txt` or `requirements-dev.txt` in the same commit.
Never commit `.venv/`.

## Team Contract

Our team contract can be found [here](docs/contract/team-contract.docx).

## Project Proposal

Our project proposal can be found [here](docs/proposal/project-proposal.docx)
