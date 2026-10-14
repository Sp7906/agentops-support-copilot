import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).resolve().parents[1]
DB_PATH = os.getenv("DB_PATH", str(BASE / "data" / "app.db"))

AOAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AOAI_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AOAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
AOAI_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

REFUND_LIMIT = float(os.getenv("REFUND_LIMIT", "5000"))
REFUND_WINDOW_DAYS = 30
PRICE_IN_PER_M = float(os.getenv("PRICE_IN_PER_M", "2.5"))
PRICE_OUT_PER_M = float(os.getenv("PRICE_OUT_PER_M", "10.0"))
