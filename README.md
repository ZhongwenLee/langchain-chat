# langchain-chat

A clean project scaffold for a LangChain-based chat application.

## Project structure

- `src/` application source code
- `tests/` automated tests
- `config/` configuration files
- `data/` local data and fixtures
- `scripts/` utility scripts
- `docs/` project documentation

## Getting started

Create an isolated environment with `uv`:

```bash
uv venv
uv sync --extra dev
```

Run the test suite:

```bash
uv run pytest
```

Run the full end-to-end self-check script:

```bash
uv run python scripts/full_self_test.py
```

This script exercises the current core flow end to end, including users, sessions, messages, storage, and chat engine behavior. It is useful when you want a fast manual verification before pushing changes.

Run the linter:

```bash
uv run ruff check .
```

## Environment setup

Copy `.env.example` to `.env` and configure your local values.
