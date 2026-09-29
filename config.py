import os
from dotenv import load_dotenv

load_dotenv()

STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "283045")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///crankspan.db")
SECRET_KEY = os.getenv("SECRET_KEY", "crankspan_secret_key_change_me_in_prod")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")
PORT = int(os.getenv("PORT", "8000"))
STRAVA_WEBHOOK_VERIFY_TOKEN = os.getenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "crankspan_strava_webhook_secret_token")
