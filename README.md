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

$ uv run app/agent.py 
```

## Running Indexer:

```bash
$ uv run app/indexer.py - Manual indexing.
```

## Running Agent:

```bash
$ uv run app/agent.py dev - Development and Debugging
$ uv run app/agent.py console - Console only.
$ uv run app/agent.py start - Production version.
```

## Running tests:

```bash
$ uv run pytest
```

