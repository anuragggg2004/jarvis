# db_init.py
import asyncio
import os
import secrets
import asyncpg
from core.config import settings
from core.security.crypto import JarvisCrypto, hash_password

async def initialize():
    print("Connecting to database to run migrations...")
    # Read schema SQL
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r") as f:
        schema_sql = f.read()

    # Wait for PostgreSQL to be ready
    retries = 10
    conn = None
    for i in range(retries):
        try:
            conn = await asyncpg.connect(settings.DATABASE_URL)
            print("Database connected successfully.")
            break
        except Exception as e:
            print(f"Database connection failed (attempt {i+1}/{retries}): {e}")
            await asyncio.sleep(3)

    if conn is None:
        raise Exception("Could not connect to the database after several retries.")

    # Execute schema
    await conn.execute(schema_sql)
    print("Database schema applied.")

    # Check if default user exists
    user = await conn.fetchrow("SELECT * FROM jarvis_user WHERE username = 'anurag'")
    if not user:
        print("Seeding default user 'anurag'...")
        password = settings.MASTER_PASSPHRASE
        pwd_hash = hash_password(password)

        # Generate salt and derived key
        salt = secrets.token_bytes(16)
        crypto = JarvisCrypto(password.encode(), salt)
        derived_key = crypto.master_key

        await conn.execute(
            """INSERT INTO jarvis_user (
                username, email, password_hash, master_key_salt, encrypted_master_key
               ) VALUES ($1, $2, $3, $4, $5)""",
            "anurag",
            "anurag@jarvis.local",
            pwd_hash,
            salt,
            derived_key
        )
        print("Default user 'anurag' seeded successfully.")
    else:
        print("Default user 'anurag' already exists.")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(initialize())
