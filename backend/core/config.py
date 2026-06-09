import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://anurag:jarvis@localhost:5432/jarvis")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")
    MASTER_PASSPHRASE: str = os.getenv("MASTER_PASSPHRASE", "password123")  # Used for dev environment key derivation
    ACCESS_TOKEN_EXPIRE_HOURS: int = 24
    OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    OLLAMA_LLM_MODEL: str = os.getenv("OLLAMA_LLM_MODEL", "llama3.2:latest")
    OLLAMA_EMBED_MODEL: str = os.getenv("OLLAMA_EMBED_MODEL", "mxbai-embed-large")

    class Config:
        env_file = ".env"

settings = Settings()
