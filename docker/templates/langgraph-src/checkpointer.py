import os
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

DB_PATH = os.environ.get(
    "LANGGRAPH_CHECKPOINT_PATH",
    "/data/langgraph/checkpoints.sqlite",
)


async def create_checkpointer() -> AsyncSqliteSaver:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(DB_PATH)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    return AsyncSqliteSaver(conn)
