import os

BOT_TOKEN = os.getenv("IRU_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_TELEGRAM_ID = int(os.getenv("IRU_ADMIN_TG_ID", "0"))
DB_PATH = os.getenv("IRU_DB_PATH", "/opt/iru/app/server/iru.db")
IRU_SERVER_URL = os.getenv("IRU_SERVER_URL", "http://localhost:8000")
