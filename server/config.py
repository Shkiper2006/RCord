import os

HOST = os.getenv("RCORD_HOST", "0.0.0.0")
PORT = int(os.getenv("RCORD_PORT", "8765"))
MEDIA_PORT = int(os.getenv("RCORD_MEDIA_PORT", str(PORT + 1)))
DB_PATH = os.getenv("RCORD_DB_PATH", "DB.dat")
HEARTBEAT_TIMEOUT = int(os.getenv("RCORD_HEARTBEAT_TIMEOUT", "60"))
CHECK_INTERVAL = int(os.getenv("RCORD_CHECK_INTERVAL", "10"))
