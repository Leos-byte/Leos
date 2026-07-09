"""`leos serve` entry point: uvicorn wrapper around the FastAPI service.

The server keeps every gate on the existing path: ``create_app`` fails closed
without ``LEOS_SERVER_API_KEY``, and secrets come from the environment only
(see ``config.py``). Multi-worker mode uses a uvicorn app factory, so worker
processes rebuild the app from the same environment/TOML configuration.
"""

from __future__ import annotations

import importlib
import os
from typing import Any

from .app import ServerUnavailable, create_app
from .config import ServerConfig, load_server_config, summarize_config


def build_app() -> Any:
    """Uvicorn factory target: rebuild the app from environment configuration."""
    config = load_server_config()
    return create_app(data_dir=config.data_dir, inbox_dir=config.inbox_dir)


def check_config(config: ServerConfig) -> int:
    """Validate configuration and the app's fail-closed startup, then exit."""
    print(summarize_config(config))
    create_app(data_dir=config.data_dir, inbox_dir=config.inbox_dir)
    print("configuration ok")
    return 0


def run_server(config: ServerConfig) -> int:
    try:
        uvicorn = importlib.import_module("uvicorn")
    except ImportError as exc:
        raise ServerUnavailable(
            "leos serve requires the optional 'server' extra: pip install 'leos-agent[server]'"
        ) from exc

    # Fail closed before binding: surfaces a missing API key immediately.
    create_app(data_dir=config.data_dir, inbox_dir=config.inbox_dir)
    print(summarize_config(config))

    # Worker processes rebuild the app via the factory, so propagate the
    # resolved settings through the environment they will read.
    os.environ["LEOS_SERVER_DATA_DIR"] = str(config.data_dir)
    if config.inbox_dir is not None:
        os.environ["LEOS_SERVER_INBOX_DIR"] = str(config.inbox_dir)

    uvicorn.run(
        "leos_agent.server.run:build_app",
        factory=True,
        host=config.host,
        port=config.port,
        workers=config.workers,
    )
    return 0
