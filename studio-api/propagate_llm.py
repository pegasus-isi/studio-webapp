"""CLI entry point for propagating LLM config to all installed tools."""

from __future__ import annotations

import asyncio

from db import Database
from llm.propagator import LLMPropagator


async def main() -> None:
    db = Database()
    await db.connect()

    propagator = LLMPropagator()
    await propagator.propagate(db)

    await db.close()
    print("LLM config propagated to all installed tools.")


if __name__ == "__main__":
    asyncio.run(main())
