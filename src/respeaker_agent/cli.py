"""Entrypoint: run the web UI + device link."""

from __future__ import annotations

import logging

import uvicorn

from .config import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.load()
    uvicorn.run(
        "respeaker_agent.web:app",
        host=settings.web_host,
        port=settings.web_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
