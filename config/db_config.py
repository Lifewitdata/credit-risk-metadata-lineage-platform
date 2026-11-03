"""Read credentials locally; never commit real database URLs."""
import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine


def get_engine(echo=False):
    load_dotenv(Path(__file__).with_name('.env'), override=False)
    url = os.environ.get('METADATA_DATABASE_URL')
    if not url:
        raise RuntimeError('Set METADATA_DATABASE_URL or copy config/.env.example to config/.env.')
    if not url.startswith('mysql+pymysql://'):
        raise ValueError('Use a mysql+pymysql:// URL for this MySQL project.')
    return create_engine(url, echo=echo, pool_pre_ping=True)
