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

The application now supports three isolated environments: `dev`, `test`, and `prod`.

### Environment precedence

1. Base configuration files: `config.yaml` and `.env`
2. Environment-specific overrides: `config.{env}.yaml` and `.env.{env}`
3. Process environment variables, which always override file-based values

### Switching environments

Set `APP_ENV` to one of `dev`, `test`, or `prod` before starting the app. No code changes are required.

```bash
# PowerShell
$env:APP_ENV = "test"
uv run pytest
```

```bash
# Bash
APP_ENV=prod uv run python scripts/full_self_test.py
```

### File layout

- `config.yaml` is the shared baseline
- `config.dev.yaml`, `config.test.yaml`, `config.prod.yaml` provide environment-specific overrides
- `.env` is the shared baseline for secrets
- `.env.dev`, `.env.test`, `.env.prod` provide environment-specific secrets and database URLs

### Isolation guarantees

- `dev`, `test`, and `prod` use separate database URLs by default
- secret values are loaded from the matching `.env.{env}` file when present
- the configuration loader deep-merges nested dictionaries so you only need to write the differences in each environment file

Copy `.env.example` to `.env` for the base fallback values, then add the environment-specific files you need.
