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

Run the linter:

```bash
uv run ruff check .
```

## Environment setup

Copy `.env.example` to `.env` and configure your local values.
