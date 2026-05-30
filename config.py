"""Runtime configuration loaded from environment variables or .env file."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root if present
load_dotenv(Path(__file__).parent / ".env")

# ── Database backend ──────────────────────────────────────────────────
# "excel"  — local data.xlsx (default, zero setup)
# "mysql"  — MySQL / MariaDB server
DB_BACKEND = os.getenv("DB_BACKEND", "excel").lower()

# ── MySQL settings (only used when DB_BACKEND=mysql) ──────────────────
MYSQL_HOST     = os.getenv("MYSQL_HOST", "localhost")
MYSQL_PORT     = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER     = os.getenv("MYSQL_USER", "pos_user")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "pos")

# ── App settings ──────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
