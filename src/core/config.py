import os
from pathlib import Path
from dotenv import load_dotenv

# Load from src/.env
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DB_NAME     = os.getenv("DB_NAME", "praxis")
DB_USER     = os.getenv("DB_USER", "praxis_user")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", 5432))
