"""CLI script to initialize the studio database schema."""

from __future__ import annotations

import asyncio

from db import Database


async def main() -> None:
    db = Database()
    await db.connect()
    await db.close()
    print("Database initialized successfully.")


if __name__ == "__main__":
    asyncio.run(main())
