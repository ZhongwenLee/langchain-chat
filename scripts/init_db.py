from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from src.config_manager import ConfigManager
from src.storage import SQLiteStorageBackend


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize the local SQLite database.")
    parser.add_argument("--base-path", default=str(Path.cwd()), help="Project base path containing .env and config/")
    return parser


async def main_async(base_path: str) -> None:
    config = ConfigManager(base_path=base_path).load()
    backend = SQLiteStorageBackend(database_url=config.secrets.database_url, config=config)
    await backend.aconnect()
    await backend.aclose()
    print(f"Database initialized: {config.secrets.database_url}")


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(main_async(args.base_path))


if __name__ == "__main__":
    main()
