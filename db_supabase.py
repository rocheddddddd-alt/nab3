"""
Database adapter with Supabase + SQLite fallback.
Tries Supabase first; if unavailable/misconfigured, falls back to SQLite.
"""
import os
import re
import sqlite3
import logging

SUPABASE_URL = os.environ.get('SUPABASE_URL', '').strip()
SUPABASE_KEY = os.environ.get('SUPABASE_SECRET_KEY', '').strip()
SQLITE_PATH = os.path.join(os.path.dirname(__file__), 'database.db')

# PostgreSQL direct connection (Supabase or any Postgres)
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
PGHOST     = os.environ.get('PGHOST', '').strip()
PGPORT     = os.environ.get('PGPORT', '5432').strip()
PGDATABASE = os.environ.get('PGDATABASE', '').strip()
PGUSER     = os.environ.get('PGUSER', '').strip()
PGPASSWORD = os.environ.get('PGPASSWORD', '').strip()
_USE_POSTGRES = bool(DATABASE_URL or (PGHOST and PGDATABASE and PGUSER))

# ──────────────────────────────────────────────────────────────────────────────
# Conflict-resolution targets for INSERT OR REPLACE → PostgreSQL upsert
# ──────────────────────────────────────────────────────────────────────────────
_CONFLICT_TARGETS = {
    'app_settings':      ['key'],
    'user_points':       ['user_id'],
    'post_test_status':  ['user_id'],
    'user_health_stats': ['user_id'],
    'custom_questions':  ['q_number'],
    'video_watches':     ['user_id', 'material_id'],
}


# ──────────────────────────────────────────────────────────────────────────────
# SQL translation helpers (SQLite → PostgreSQL)
# ──────────────────────────────────────────────────────────────────────────────
def _translate(sql: str) -> str:
    def replace_upsert(m):
        table = m.group(1).strip()
        rest = m.group(2)
        cols_match = re.search(r'\(([^)]+)\)\s*VALUES', rest, re.IGNORECASE)
        if cols_match and table in _CONFLICT_TARGETS:
            cols = [c.strip() for c in cols_match.group(1).split(',')]
            targets = _CONFLICT_TARGETS[table]
            update_cols = [c for c in cols if c not in targets]
            if update_cols:
                set_clause = ', '.join(f'{c}=EXCLUDED.{c}' for c in update_cols)
                conflict = f'ON CONFLICT ({", ".join(targets)}) DO UPDATE SET {set_clause}'
            else:
                conflict = f'ON CONFLICT ({", ".join(targets)}) DO NOTHING'
            return f'INSERT INTO {table} {rest} {conflict}'
        return f'INSERT INTO {table} {rest} ON CONFLICT DO NOTHING'

    sql = re.sub(
        r'INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)(.*)',
        replace_upsert, sql, flags=re.IGNORECASE | re.DOTALL
    )
    sql = re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO', 'INSERT INTO', sql, flags=re.IGNORECASE)
    sql_upper = sql.upper().strip()
    if sql_upper.startswith('INSERT INTO') and 'ON CONFLICT' not in sql_upper:
        sql = sql.rstrip('; \n') + ' ON CONFLICT DO NOTHING'

    # SQLite → PostgreSQL type conversions for CREATE TABLE
    sql = re.sub(r'\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b',
                 'SERIAL PRIMARY KEY', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bAUTOINCREMENT\b', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bDATETIME\b', 'TIMESTAMP', sql, flags=re.IGNORECASE)

    # SQLite date() functions → PostgreSQL equivalents
    sql = re.sub(
        r"date\s*\(\s*'now'\s*,\s*['\"]?-(\d+)\s+days['\"]?\s*\)",
        lambda m: f"(CURRENT_DATE - INTERVAL '{m.group(1)} days')",
        sql, flags=re.IGNORECASE
    )
    sql = re.sub(r"date\s*\(\s*'now'\s*\)", 'CURRENT_DATE', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bdate\s*\((\w+)\)', r'DATE(\1)', sql, flags=re.IGNORECASE)

    # Subquery without alias (PostgreSQL requires alias for derived tables)
    sql = re.sub(r'\)\s+WHERE\b', ') AS _sub WHERE', sql, flags=re.IGNORECASE)

    return sql


def _bind(sql: str, params) -> str:
    if not params:
        return sql
    params = list(params)
    parts, idx, i = [], 0, 0
    while i < len(sql):
        if sql[i] == '?' and idx < len(params):
            val = params[idx]; idx += 1
            if val is None:
                parts.append('NULL')
            elif isinstance(val, bool):
                parts.append('TRUE' if val else 'FALSE')
            elif isinstance(val, (int, float)):
                parts.append(str(val))
            else:
                parts.append(f"'{str(val).replace(chr(39), chr(39)*2)}'")
        else:
            parts.append(sql[i])
        i += 1
    return ''.join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Row / Result helpers (shared by both backends)
# ──────────────────────────────────────────────────────────────────────────────
class _Row(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _Result:
    def __init__(self, rows, lastrowid=None):
        self._rows = [_Row(r) if not isinstance(r, _Row) else r for r in (rows or [])]
        self._pos = 0
        self.lastrowid = lastrowid
        self.rowcount = len(self._rows)

    def fetchone(self):
        if self._pos < len(self._rows):
            row = self._rows[self._pos]; self._pos += 1; return row
        return None

    def fetchall(self):
        rows = self._rows[self._pos:]; self._pos = len(self._rows); return rows

    def __iter__(self):
        return iter(self._rows)

    def __bool__(self):
        return bool(self._rows)


# ──────────────────────────────────────────────────────────────────────────────
# SQLite backend (local fallback)
# ──────────────────────────────────────────────────────────────────────────────
class SQLiteDB:
    def __init__(self, path=SQLITE_PATH):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._last = None

    def execute(self, sql: str, params=()):
        cur = self._conn.execute(sql, params)
        rows = []
        try:
            raw = cur.fetchall()
            rows = [_Row(dict(zip(r.keys(), tuple(r)))) for r in raw]
        except Exception:
            pass
        self._last = _Result(rows, cur.lastrowid)
        return self._last

    def fetchone(self):
        return self._last.fetchone() if self._last else None

    def fetchall(self):
        return self._last.fetchall() if self._last else []

    @property
    def lastrowid(self):
        return self._last.lastrowid if self._last else None

    def cursor(self):
        return self

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# PostgreSQL direct backend (psycopg2)
# ──────────────────────────────────────────────────────────────────────────────
class PostgresDB:
    def __init__(self):
        import psycopg2
        import psycopg2.extras
        cursor_factory = psycopg2.extras.RealDictCursor
        conn = None

        if DATABASE_URL:
            dsn = DATABASE_URL
            if dsn.startswith('postgres://'):
                dsn = 'postgresql://' + dsn[len('postgres://'):]
            for ssl in ('prefer', 'disable'):
                try:
                    conn = psycopg2.connect(dsn, sslmode=ssl, cursor_factory=cursor_factory)
                    break
                except Exception:
                    continue
        else:
            for ssl in ('prefer', 'disable'):
                try:
                    conn = psycopg2.connect(
                        host=PGHOST, port=int(PGPORT), dbname=PGDATABASE,
                        user=PGUSER, password=PGPASSWORD, sslmode=ssl,
                        cursor_factory=cursor_factory
                    )
                    break
                except Exception:
                    continue

        if conn is None:
            raise Exception("Cannot connect to PostgreSQL")

        self._conn = conn
        self._conn.autocommit = True   # each statement is its own transaction
        self._last = None

    def execute(self, sql: str, params=()):
        sql = _translate(sql)
        sql = _bind(sql, params)
        cur = self._conn.cursor()
        cur.execute(sql)
        try:
            rows = [_Row(dict(r)) for r in (cur.fetchall() or [])]
        except Exception:
            rows = []
        lastrowid = None
        if sql.strip().upper().startswith('INSERT'):
            try:
                cur2 = self._conn.cursor()
                cur2.execute('SELECT lastval()')
                row = cur2.fetchone()
                if row:
                    lastrowid = list(row.values())[0]
            except Exception:
                pass
        self._last = _Result(rows, lastrowid)
        return self._last

    def fetchone(self):
        return self._last.fetchone() if self._last else None

    def fetchall(self):
        return self._last.fetchall() if self._last else []

    @property
    def lastrowid(self):
        return self._last.lastrowid if self._last else None

    def cursor(self):
        return self

    def commit(self):
        try:
            self._conn.commit()
        except Exception:
            pass

    def close(self):
        try:
            self._conn.commit()
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Supabase backend (via exec_sql RPC)
# ──────────────────────────────────────────────────────────────────────────────
class SupabaseDB:
    def __init__(self):
        from supabase import create_client
        self._client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self._last = None

    def execute(self, sql: str, params=()):
        sql = _translate(sql)
        sql = _bind(sql, params)
        try:
            resp = self._client.rpc('exec_sql', {'sql': sql}).execute()
        except Exception as exc:
            raise Exception(f'Supabase RPC error: {exc}\nSQL: {sql[:200]}')
        data = resp.data
        lastrowid, rows = None, []
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            if 'error' in data:
                raise Exception(f"SQL error: {data['error']}\nSQL: {sql[:200]}")
            if 'id' in data:
                lastrowid = data['id']
            if 'ok' not in data:
                rows = [data]
        self._last = _Result(rows, lastrowid)
        return self._last

    def fetchone(self):
        return self._last.fetchone() if self._last else None

    def fetchall(self):
        return self._last.fetchall() if self._last else []

    @property
    def lastrowid(self):
        return self._last.lastrowid if self._last else None

    def cursor(self):
        return self

    def commit(self):
        pass

    def close(self):
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Factory — tries PostgreSQL → Supabase REST → SQLite
# ──────────────────────────────────────────────────────────────────────────────
_POSTGRES_OK  = None   # None = untested, True = works, False = broken
_SUPABASE_OK  = None


def get_db():
    global _POSTGRES_OK, _SUPABASE_OK

    # ── 1. Supabase REST API (priority: user's Supabase project) ─────────────
    if SUPABASE_URL and SUPABASE_KEY:
        if _SUPABASE_OK is None:
            try:
                db = SupabaseDB()
                db.execute("SELECT 1 AS ok")
                result = db._last
                if result is not None:
                    _SUPABASE_OK = True
                    logging.warning("✅ Connected to Supabase")
                else:
                    raise Exception("exec_sql returned no result")
            except Exception as e:
                _SUPABASE_OK = False
                logging.warning(f"⚠️  Supabase unavailable (run exec_sql setup SQL): {e}")

        if _SUPABASE_OK:
            try:
                return SupabaseDB()
            except Exception:
                pass

    # ── 2. PostgreSQL direct (Replit internal fallback) ───────────────────────
    if _USE_POSTGRES:
        if _POSTGRES_OK is None:
            try:
                db = PostgresDB()
                db.execute("SELECT 1 AS ok")
                db.commit()
                _POSTGRES_OK = True
                logging.warning("✅ Connected to Replit PostgreSQL (fallback)")
            except Exception as e:
                _POSTGRES_OK = False
                logging.warning(f"⚠️  Replit PostgreSQL unavailable: {e}")

        if _POSTGRES_OK:
            try:
                return PostgresDB()
            except Exception:
                _POSTGRES_OK = False

    # ── 3. SQLite local fallback ──────────────────────────────────────────────
    logging.warning("⚠️  Using SQLite local database")
    return SQLiteDB()


# ──────────────────────────────────────────────────────────────────────────────
# Supabase Storage helpers
# ──────────────────────────────────────────────────────────────────────────────
def storage_url(bucket: str, filename: str) -> str:
    """Return a public URL for a file in Supabase Storage (falls back to /static/uploads/)."""
    if not filename:
        return ''
    if SUPABASE_URL and SUPABASE_KEY:
        return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{filename}"
    folder = bucket.replace('-', '_')
    return f'/static/uploads/{folder}/{filename}'


def upload_to_storage(bucket: str, filename: str, file_bytes: bytes,
                      content_type: str = 'application/octet-stream') -> bool:
    """Upload bytes to a Supabase Storage bucket. Returns True on success."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        from supabase import create_client
        c = create_client(SUPABASE_URL, SUPABASE_KEY)
        c.storage.from_(bucket).upload(
            path=filename,
            file=file_bytes,
            file_options={'content-type': content_type, 'upsert': 'true'}
        )
        return True
    except Exception as e:
        logging.warning(f"Storage upload failed ({bucket}/{filename}): {e}")
        return False


def delete_from_storage(bucket: str, filename: str) -> None:
    """Delete a file from Supabase Storage (silent failure)."""
    if not filename or not SUPABASE_URL or not SUPABASE_KEY:
        return
    try:
        from supabase import create_client
        c = create_client(SUPABASE_URL, SUPABASE_KEY)
        c.storage.from_(bucket).remove([filename])
    except Exception as e:
        logging.warning(f"Storage delete failed ({bucket}/{filename}): {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Supabase setup helper — run once to create exec_sql RPC
# ──────────────────────────────────────────────────────────────────────────────
EXEC_SQL_FUNCTION = """
-- Run this ONCE in your Supabase SQL Editor to enable exec_sql RPC
CREATE OR REPLACE FUNCTION exec_sql(sql text)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  result jsonb;
BEGIN
  EXECUTE sql;
  BEGIN
    EXECUTE 'SELECT jsonb_agg(row_to_json(t)) FROM (' || sql || ') t' INTO result;
  EXCEPTION WHEN OTHERS THEN
    result := '[]'::jsonb;
  END;
  RETURN COALESCE(result, '[]'::jsonb);
END;
$$;
"""
