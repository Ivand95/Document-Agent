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
  api/            routers: auth, chat (HTTP + WebSocket), admin/ingestion; deps.py holds auth dependencies
  core/           config, security (JWT, sessions), llm, prompt_safety
  agents/         common/rag.py, documents/graph.py, audio/graph.py (LangGraph pipelines)
  integrations/   m365.py (Microsoft login and Graph profile)
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

## Running Agent in the console:

```bash
$ PYTHONPATH=app uv run python -m agents.documents.graph  # documents
$ PYTHONPATH=app uv run python -m agents.audio.graph      # calls
```

## Running tests:

```bash
$ uv run pytest
```

