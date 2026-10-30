"""Bootstrap an empty MySQL schema or add controls to an existing one.
Uses METADATA_DATABASE_URL; the database must already exist. No credentials created.
"""
import argparse
import sys
from pathlib import Path
from sqlalchemy import inspect
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config.db_config import get_engine
from metadata.migrate import migrate
ROOT=Path(__file__).resolve().parents[1]

def statements(script):
    delimiter=';';buffer=[]
    for line in script.splitlines():
        if line.strip().startswith('--') or not line.strip():continue
        if line.upper().startswith('DELIMITER '):
            delimiter=line.split()[1];continue
        buffer.append(line)
        if line.rstrip().endswith(delimiter):
            yield '\n'.join(buffer).rstrip()[:-len(delimiter)]
            buffer=[]
    if buffer:raise ValueError('Unterminated SQL statement')

def setup(engine,upgrade=False):
    with engine.begin() as conn:
        exists='data_dictionary' in inspect(conn).get_table_names()
        if exists and not upgrade:raise ValueError('Schema already exists. Back it up and use --upgrade.')
        if not exists:
            for stmt in statements((ROOT/'sql/ddl/01_create_schema.sql').read_text()):
                if not stmt.lstrip().upper().startswith(('CREATE DATABASE','USE ')):
                    conn.exec_driver_sql(stmt)
    migrate(engine)
    with engine.begin() as conn:
        for filename in ['sql/validation/02_dictionary_reconciliation.sql','sql/ddl/03_audit_triggers.sql']:
            for stmt in statements((ROOT/filename).read_text()):
                if not stmt.lstrip().upper().startswith(('USE ','REVOKE ')):
                    conn.exec_driver_sql(stmt)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--upgrade',action='store_true');a=p.parse_args()
    engine=get_engine();setup(engine,a.upgrade);engine.dispose()
