"""SQLite storage; every read-modify-write decision uses BEGIN IMMEDIATE."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as con:
            con.execute('PRAGMA journal_mode=WAL')
            version = con.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError(f'Unsupported database schema version {version}; migration required')
            if version == 2:
                return
            # Version 2 adds GitHub integration tables without rewriting any
            # recorded runs, evidence, retry windows, or existing configuration.
            con.executescript('''
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS dependencies (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE, request_hash TEXT NOT NULL,
                    data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                    attempt_number INTEGER NOT NULL, idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL, data TEXT NOT NULL, response TEXT NOT NULL,
                    UNIQUE(run_id, attempt_number), UNIQUE(run_id, idempotency_key));
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                    data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS configuration (key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS github_connections (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL, token_ciphertext TEXT,
                    source TEXT NOT NULL, expires_at REAL);
                CREATE TABLE IF NOT EXISTS github_projects (
                    id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, repository_id INTEGER NOT NULL,
                    data TEXT NOT NULL, UNIQUE(connection_id, repository_id));
                CREATE TABLE IF NOT EXISTS github_devices (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS github_project_tokens (
                    project_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                    dependency_ids TEXT NOT NULL);
                PRAGMA user_version=2;
                COMMIT;
            ''')

    def connect(self):
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA busy_timeout=30000')
        return con

    @contextmanager
    def transaction(self):
        con = self.connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def get_demo_policy(self):
        with self.transaction() as con:
            row = con.execute("SELECT data FROM configuration WHERE key='demo_policy'").fetchone()
            return json.loads(row['data']) if row else {}

    def set_demo_policy(self, policy):
        with self.transaction() as con:
            con.execute("INSERT INTO configuration VALUES ('demo_policy', ?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", (dump(policy),))

    @staticmethod
    def get(con, table, identifier):
        assert table in ('runs', 'dependencies')
        row = con.execute(f'SELECT data FROM {table} WHERE id=?', (identifier,)).fetchone()
        return json.loads(row['data']) if row else None

    @staticmethod
    def save(con, table, value):
        assert table in ('runs', 'dependencies')
        con.execute(f'UPDATE {table} SET data=? WHERE id=?', (dump(value), value['id']))

    @staticmethod
    def audit(con, action, detail, **extra):
        record = {'created_at': now(), 'action': action, 'detail': detail, **extra}
        con.execute('INSERT INTO audit(data) VALUES (?)', (dump(record),))
