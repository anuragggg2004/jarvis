import asyncpg

async def init_db(database_url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(database_url)
