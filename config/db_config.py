"""
db_config.py
------------
Central place that builds the SQLAlchemy engine used by every script in
this platform (ETL, metadata service, lineage builder, docs generator).

Enterprise practice: credentials are NEVER hardcoded or committed. They are
read from environment variables (populated locally via a .env file, and in
a real deployment via a secrets manager / CI variable injection).
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

# Load variables from a local .env file if present (developer convenience).
# In production this step is a no-op -- real secrets come from the environment.
_ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)


def _get_env(var_name: str, default: str | None = None) -> str:
    value = os.getenv(var_name, default)
    if value is None:
        raise EnvironmentError(
            f"Missing required environment variable: {var_name}. "
            f"Copy config/.env.example to config/.env and fill it in."
        )
    return value


def get_engine(echo: bool = False) -> Engine:
    """
    Build and return a SQLAlchemy engine for the metadata platform database.

    pool_pre_ping=True: avoids 'MySQL server has gone away' errors on
    long-running lineage/documentation jobs by validating the connection
    before each checkout from the pool -- standard practice for batch ETL.
    """
    user = _get_env("DB_USER")
    password = _get_env("DB_PASSWORD")
    host = _get_env("DB_HOST", "localhost")
    port = _get_env("DB_PORT", "3306")
    database = _get_env("DB_NAME", "credit_metadata_platform")

    connection_url = (
        f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"
    )

    return create_engine(connection_url, echo=echo, pool_pre_ping=True, future=True)
