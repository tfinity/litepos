"""Database backend dispatcher — routes to excel_db or mysql_db based on DB_BACKEND config."""

from config import DB_BACKEND

if DB_BACKEND == "mysql":
    from mysql_db import *  # noqa: F401, F403
else:
    from excel_db import *  # noqa: F401, F403
