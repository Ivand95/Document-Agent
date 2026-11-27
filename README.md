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
  api/            routers: auth, chat (HTTP + WebSocket), me, admin/{ingestion,users,rbac,audit,capabilities};
                  deps.py holds the auth and permission dependencies (require(action, type, id))
  core/           config, security (JWT, sessions), llm, prompt_safety
  agents/         common/rag.py, documents/graph.py, audio/graph.py (LangGraph pipelines)
  integrations/   m365.py (Microsoft login and Graph profile)
  users/          users registered by their Microsoft 365 login
  rbac/           roles, permissions and audit log: evaluation (model.py), data (service.py), bootstrap CLI
  capabilities/   registry of tools, MCP servers and CLIs (registry.py), the gateway every call goes through
                  (gateway.py), encrypted connection secrets, native tools shipped in capabilities/tools/
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

## Capabilities (tools, MCP servers, CLIs)

Everything an agent can call is registered in `capabilities` (migration 0006) and managed from
`/admin/capabilities` (admin area `capabilities`, held by the `admin` role). A call runs only if the
capability is enabled, the user has `use` on it in the permission matrix (resource type `capability`), the
operation is on its whitelist and enabled, it is marked read-only, and its arguments match the schema.
Everything new starts disabled. Native tools are Python modules in `app/capabilities/tools/` (or packages
with a `coop_agent.tools` entry point) and are copied into the registry at start-up. Secrets are encrypted
with `CAPABILITY_SECRET_KEY` and never returned by the API.

```bash
$ PYTHONPATH=app uv run python -m capabilities.cli generate-key   # value for CAPABILITY_SECRET_KEY
$ PYTHONPATH=app uv run python -m capabilities.cli list
$ PYTHONPATH=app uv run python -m capabilities.cli rotate-secrets # after putting a new key first
```

## LLM models and OpenRouter

The documents and calls agents always use OpenAI (`CHAT_MODEL`, `LIGHT_MODEL`). Admin-built tools call
models from an allowlist managed at `/admin/models` (migration 0007), through `providers/gateway.py`,
which checks: the model is enabled, the user has `use` on it (resource type `model`, id
`openrouter:<vendor>/<model>`), the data it would read is at or below its `max_data_class`, and the
user's monthly budget (`llm_budget_usd_month` in the permission's constraints). OpenRouter needs
`OPENROUTER_ENABLED=true`, the provider enabled and its key set from the panel. Discovered models start
disabled, capped at `internal` data, with `data_collection: "deny"`; raising either is super_admin-only.
Every LLM call, including the core agents', is recorded in `llm_usage` (tokens and cost, never content).

### MCP servers

Register an MCP server at `/admin/capabilities` (kind `mcp`, Streamable HTTP URL; stdio only for
executables in `CAPABILITY_BIN_DIR`). Its credentials go in a connection: a token (`auth_type` `token`),
or OAuth (`auth_type` `oauth`, then `POST /admin/capabilities/connections/{id}/oauth/start` and consent in
the popup; tokens are stored encrypted and refreshed on their own). `POST /admin/capabilities/{id}/discover`
copies the server's tools into the whitelist: they start disabled and not read-only, and a whitelisted tool
whose description or schema changes upstream is disabled again. In a chat, users can switch off the MCP
servers they were given for that conversation (`GET /me/mcps`; WebSocket frames `{"type": "mcps"}` and
`{"type": "mcp_toggle", "key": ..., "enabled": false}`; `disabled_mcps` in `POST /chat`).

### CLI tools

A CLI capability (kind `cli`) is an executable in `CAPABILITY_BIN_DIR` (config `executable`: a name,
never a path). Each operation is added by hand with an argument schema and a command template, e.g.
`["insights", "--account", "{account}", ["--since={since}"]]`: the first part is fixed, `{x}` stands for
an argument, and a list is an optional group left out when its argument isn't given. Every argument the
template uses must be bounded by its schema (`enum`, `pattern` with `maxLength`, `format: date`,
numbers, booleans): no free text reaches a command line. Programs run without a shell, in a fresh
temporary directory, with a minimal environment (config `env`, plus secrets mapped from the connection
with `env_from_connection`), time, memory and output limits, and never as root
(`CAPABILITY_RUN_AS_USER`).

### Prompt tools

Admins with the `prompt_tools` admin area (roles `admin`, `tool_admin`; migration 0008) build tools
without code at `/admin/prompt-tools`: a Jinja2 template (sandboxed; every value it prints is wrapped
as `<dato>…</dato>` untrusted data), an input schema, an optional output schema, an allowlisted model and
a data class (what the tool may receive and returns, at most its model's `max_data_class`). A tool starts
disabled and can be enabled only after a successful test run (`POST /admin/prompt-tools/{id}/test`) of
its current version; changing its content needs it disabled and makes a new version. Users need `use`
on the tool (resource type `capability`) and on its model.

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

