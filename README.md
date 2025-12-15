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
$ cp env.example .env.local

## ... fill .env.local accordingly ...

$ uv run app/agent.py 
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

## Deployment:

```bash
$ lk agent create - Must be done in the root of the project. Will stop existing sessions.
$ lk agent deploy - Must be done in the root of the project. Will allow keep existing sessions alive for up to an hour.
```