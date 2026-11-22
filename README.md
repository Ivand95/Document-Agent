# Document Handler Agent
A RAG enriched chatbot for Cooperativa Barcelona.

# Requirements
- Python 3.13+
- uv

# Installation
```sh
$ git clone <repository_directory> <local_respository_name>
$ uv venv
$ source .venv/bin/activate or .venv/Scripts/activate

$ cd <local_repository_name>
$ cp env.example .env

## ... fill .env.local accordingly ...

$ uv run uvicorn main:app --app-dir app --reload
```

## Layout

`app/` is the import root (it is on `PYTHONPATH` in the Dockerfile, the jobs and the tests), so modules
import each other as `from core.config import ...`.

```
app/
  main.py         FastAPI app: lifespan, middleware, routers
  api/            routers: auth, chat (HTTP + WebSocket), me, admin/{ingestion,users,rbac,audit};
                  deps.py holds the auth and permission dependencies (require(action, type, id))
  core/           config, security (JWT, sessions), llm, prompt_safety
  agents/         common/rag.py, documents/graph.py, audio/graph.py (LangGraph pipelines)
  integrations/   m365.py (Microsoft login and Graph profile)
  users/          users registered by their Microsoft 365 login
  rbac/           roles, permissions and audit log: evaluation (model.py), data (service.py), bootstrap CLI
  ingestion/      SharePoint sync, document and audio indexers, ingestion panel state, nightly window
  db/             db_setup.py and SQL migrations
  models/         pydantic request/response schemas
jobs/indexing.py  indexer service entry point
```

## Running the API:

```bash
$ uv run uvicorn main:app --app-dir app --reload
```

## Running Indexer:

```bash
$ uv run python jobs/indexing.py                          # indexer service (nightly window)
$ PYTHONPATH=app uv run python -m ingestion.indexer       # manual document indexing
$ PYTHONPATH=app uv run python -m db.db_setup --check     # check the Supabase setup
```

## Access control (RBAC)

Users are registered by their first Microsoft 365 login. What they may do comes from roles: the ones that
follow from their department or position (`/admin/rbac/mappings`, e.g. `GR -` -> `manager`), the
`everyone` role `employee`, and roles given by hand. Migration 0005 seeds the rules that were hard-coded
before (department -> document category, managers, Tecnología as admin), so nothing changes on deploy.
Deny by default; an explicit deny beats an allow; a user's own permissions override their roles'.

```bash
$ PYTHONPATH=app uv run python -m rbac.cli grant-super-admin persona@cooperativa.com  # after their first login
$ PYTHONPATH=app uv run python -m rbac.cli effective persona@cooperativa.com          # why they can/can't
```

## Running Agent in the console:

```bash
$ PYTHONPATH=app uv run python -m agents.documents.graph  # documents
$ PYTHONPATH=app uv run python -m agents.audio.graph      # calls
```

The console uses the old department rule for document categories (CLI_DEPARTMENT, CLI_POSITION), not RBAC.

## Running tests:

```bash
$ uv run pytest
```

