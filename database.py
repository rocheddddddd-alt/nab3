# database.py — Supabase REST API backend (via exec_sql RPC)
import os
import re
import json
import base64
import threading
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# CONFIG — derive Supabase REST URL from service-role JWT
# ─────────────────────────────────────────────────────────────
SUPABASE_KEY = os.environ.get('SUPABASE_SECRET_KEY', '').strip()
SPREADSHEET_ID = os.environ.get("GOOGLE_SHEETS_ID", "")

def _get_supabase_api_url():
    if SUPABASE_KEY:
        try:
            payload = SUPABASE_KEY.split('.')[1]
            payload += '=' * (4 - len(payload) % 4)
            data = json.loads(base64.b64decode(payload).decode())
            proj_ref = data.get('ref', '')
            if proj_ref:
                return f'https://{proj_ref}.supabase.co'
        except Exception:
            pass
    fallback = os.environ.get('SUPABASE_URL', '')
    if fallback.startswith('https://'):
        return fallback
    return None

SUPABASE_API_URL = _get_supabase_api_url()
if not SUPABASE_API_URL:
    raise ValueError("⚠️ ไม่พบ Supabase API URL — กรุณาตั้ง SUPABASE_SECRET_KEY หรือ SUPABASE_URL")

_sheets_ok = False
_sheets_tried = False
_lock = threading.RLock()

# ─────────────────────────────────────────────────────────────
# TABLE SCHEMAS
# ─────────────────────────────────────────────────────────────
_SCHEMAS = [
    "CREATE TABLE IF NOT EXISTS users (id BIGSERIAL PRIMARY KEY, email TEXT UNIQUE, password TEXT, name TEXT, role TEXT DEFAULT 'user', last_seen TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS user_health_stats (id BIGSERIAL PRIMARY KEY, user_id BIGINT UNIQUE, epigenetic_age REAL, fitness_score REAL, epigenetic_pdf TEXT, inbody_pdf TEXT, biological_age REAL, age_acceleration REAL)",
    "CREATE TABLE IF NOT EXISTS user_points (user_id BIGINT PRIMARY KEY, points INTEGER DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE TABLE IF NOT EXISTS post_test_status (user_id BIGINT PRIMARY KEY, is_unlocked INTEGER DEFAULT 0)",
    "CREATE TABLE IF NOT EXISTS questionnaires (id BIGSERIAL PRIMARY KEY, user_id BIGINT, type TEXT DEFAULT 'pre', age INTEGER, bmi REAL, waist REAL, answers_json TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS exercises (id BIGSERIAL PRIMARY KEY, user_id BIGINT, type TEXT, steps INTEGER, distance REAL, duration INTEGER, calories INTEGER DEFAULT 0, date TEXT)",
    "CREATE TABLE IF NOT EXISTS daily_logs (id BIGSERIAL PRIMARY KEY, user_id BIGINT, date TEXT, sleep_hours REAL, stress_level TEXT, food_note TEXT, water_glasses INTEGER)",
    "CREATE TABLE IF NOT EXISTS social_shares (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, share_text TEXT, file_name TEXT, file_type TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS learning_materials (id BIGSERIAL PRIMARY KEY, title TEXT, type TEXT, url TEXT, category TEXT)",
    "CREATE TABLE IF NOT EXISTS lab_results (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, filename TEXT, original_name TEXT, notes TEXT, lab_type TEXT DEFAULT 'other', period TEXT DEFAULT 'pre', uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    "ALTER TABLE lab_results ADD COLUMN IF NOT EXISTS period TEXT DEFAULT 'pre'",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS occupation TEXT",
    "ALTER TABLE user_health_stats ADD COLUMN IF NOT EXISTS epigenetic_age_pre REAL",
    "ALTER TABLE user_health_stats ADD COLUMN IF NOT EXISTS epigenetic_age_post REAL",
    "ALTER TABLE user_health_stats ADD COLUMN IF NOT EXISTS fitness_score_pre REAL",
    "ALTER TABLE user_health_stats ADD COLUMN IF NOT EXISTS fitness_score_post REAL",
    "CREATE TABLE IF NOT EXISTS certificates (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, filename TEXT, original_name TEXT, notes TEXT, issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS custom_questions (id BIGSERIAL PRIMARY KEY, q_number INTEGER NOT NULL UNIQUE, dimension INTEGER NOT NULL, q_text TEXT NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE IF NOT EXISTS video_watches (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, material_id BIGINT NOT NULL, watched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, material_id))",
    "CREATE TABLE IF NOT EXISTS challenges (id BIGSERIAL PRIMARY KEY, user_id BIGINT, type TEXT, date TEXT)",
    "CREATE TABLE IF NOT EXISTS notifications (id BIGSERIAL PRIMARY KEY, user_id BIGINT, message TEXT, type TEXT, is_read INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
]

# ─────────────────────────────────────────────────────────────
# PARAM BINDING  (%s → literal value)
# ─────────────────────────────────────────────────────────────
def _bind(sql: str, params) -> str:
    if not params:
        return sql
    params = list(params)
    result, idx, i = [], 0, 0
    while i < len(sql):
        if sql[i] == '%' and i + 1 < len(sql) and sql[i + 1] == 's':
            if idx < len(params):
                val = params[idx]; idx += 1
                if val is None:
                    result.append('NULL')
                elif isinstance(val, bool):
                    result.append('TRUE' if val else 'FALSE')
                elif isinstance(val, (int, float)):
                    result.append(str(val))
                else:
                    result.append("'" + str(val).replace("'", "''") + "'")
            i += 2
        else:
            result.append(sql[i])
            i += 1
    return ''.join(result)

# ─────────────────────────────────────────────────────────────
# ROW OBJECT WITH INTEGER + KEY INDEXING
# ─────────────────────────────────────────────────────────────
class _Row(dict):
    """Dict that also supports integer indexing: row[0] returns first value."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

# ─────────────────────────────────────────────────────────────
# CURSOR-LIKE RESULT OBJECT
# ─────────────────────────────────────────────────────────────
class _Cursor:
    def __init__(self, rows, lastrowid=None):
        self._rows = rows or []
        self._pos = 0
        self.lastrowid = lastrowid
        self.rowcount = len(self._rows)

    def fetchone(self):
        if self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            return row
        return None

    def fetchall(self):
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows

    def close(self):
        pass

    def __iter__(self):
        return iter(self._rows)

# ─────────────────────────────────────────────────────────────
# DATABASE CLASS
# ─────────────────────────────────────────────────────────────
class DB:
    def __init__(self):
        self._rpc_url = f"{SUPABASE_API_URL}/rest/v1/rpc/exec_sql"
        self._headers = {
            'apikey': SUPABASE_KEY,
            'Authorization': f'Bearer {SUPABASE_KEY}',
            'Content-Type': 'application/json',
        }
        print(f"🔧 Connecting to Supabase at {SUPABASE_API_URL}...")
        self._init_tables()
        print("✅ Database initialized via Supabase REST API!")

    def _exec_sql(self, sql: str):
        """Execute raw SQL via exec_sql RPC using HTTP/1.1 (requests library).
        Uses a fresh connection per call to avoid connection state/pooling issues."""
        import requests as _req
        for attempt in range(3):
            try:
                resp = _req.post(
                    self._rpc_url,
                    headers=self._headers,
                    json={'sql': sql},
                    timeout=30
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        return [_Row(r) for r in data]
                    elif isinstance(data, dict):
                        if 'error' in data:
                            raise Exception(f"SQL error: {data['error']}\nSQL: {sql[:300]}")
                        return [_Row(data)] if data else []
                    return []
                else:
                    raise Exception(f"RPC HTTP {resp.status_code}: {resp.text[:300]}")
            except Exception as e:
                err_str = str(e)
                is_conn = any(x in err_str for x in ['ConnectionError', 'Timeout', 'RemoteProtocol'])
                if is_conn and attempt < 2:
                    continue
                raise

    def _init_tables(self):
        """Create all tables if they don't exist."""
        print("📋 Verifying database tables...")
        for schema in _SCHEMAS:
            try:
                self._exec_sql(schema)
            except Exception as e:
                print(f"⚠️ Table init note: {e}")
        print("✅ Tables verified!")

    def execute(self, sql: str, params=()):
        """Execute SQL with %s params; returns cursor-like object."""
        bound = _bind(sql, params)
        try:
            rows = self._exec_sql(bound)
        except Exception as e:
            print(f"❌ SQL Error: {e}")
            raise

        lastrowid = None
        if re.match(r'^\s*INSERT\b', sql, re.IGNORECASE):
            try:
                seq_rows = self._exec_sql("SELECT lastval() AS lastid")
                if seq_rows:
                    lastrowid = seq_rows[0].get('lastid')
            except Exception:
                pass

        return _Cursor(rows, lastrowid)

    def fetchall(self, sql: str, params=()):
        return self.execute(sql, params).fetchall()

    def fetchone(self, sql: str, params=()):
        return self.execute(sql, params).fetchone()

    def commit(self):
        pass  # exec_sql auto-commits each statement

    def close(self):
        pass  # HTTP client, no persistent connection to close

    # ── Google Sheets (optional) ─────────────────────────────
    def _try_sheets(self):
        global _sheets_tried, _sheets_ok
        _sheets_tried = True
        try:
            if SPREADSHEET_ID:
                _sheets_ok = True
                print("✅ Google Sheets configured")
            else:
                print("⚠️ GOOGLE_SHEETS_ID not set — Sheets sync disabled")
        except Exception as e:
            _sheets_ok = False
            print(f"⚠️ Google Sheets error: {e}")

# ─────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────
_db = None
_db_lock = threading.Lock()

def get_db():
    global _db
    with _db_lock:
        if _db is None:
            _db = DB()
        return _db

def init_db():
    print("🚀 Initializing database...")
    return get_db()

# ─────────────────────────────────────────────────────────────
# UTILITY HELPERS
# ─────────────────────────────────────────────────────────────
def reload_from_sheets():
    return True

def sheets_status():
    return {"connected": _sheets_ok, "spreadsheet_id": SPREADSHEET_ID}

def setup_google_sheets():
    return reload_from_sheets()

def storage_url(bucket, filename):
    if not filename:
        return ''
    if SUPABASE_API_URL and SUPABASE_KEY:
        return f'{SUPABASE_API_URL}/storage/v1/object/public/{bucket}/{filename}'
    return f'/static/uploads/{bucket.replace("-", "_")}/{filename}'

def upload_to_storage(bucket, filename, file_bytes, content_type=''):
    import requests as _req
    local_path = os.path.join('static', 'uploads', bucket.replace('-', '_'))
    os.makedirs(local_path, exist_ok=True)
    try:
        with open(os.path.join(local_path, filename), 'wb') as f:
            f.write(file_bytes)
    except Exception:
        pass
    if SUPABASE_API_URL and SUPABASE_KEY:
        try:
            url = f'{SUPABASE_API_URL}/storage/v1/object/{bucket}/{filename}'
            headers = {
                'apikey': SUPABASE_KEY,
                'Authorization': f'Bearer {SUPABASE_KEY}',
                'Content-Type': content_type or 'application/octet-stream',
                'x-upsert': 'true',
            }
            r = _req.put(url, data=file_bytes, headers=headers, timeout=30)
            return r.status_code in (200, 201)
        except Exception:
            pass
    return True

def delete_from_storage(bucket, filename):
    import requests as _req
    local = os.path.join('static', 'uploads', bucket.replace('-', '_'), filename)
    if os.path.exists(local):
        try:
            os.remove(local)
        except Exception:
            pass
    if SUPABASE_API_URL and SUPABASE_KEY and filename:
        try:
            url = f'{SUPABASE_API_URL}/storage/v1/object/{bucket}/{filename}'
            headers = {
                'apikey': SUPABASE_KEY,
                'Authorization': f'Bearer {SUPABASE_KEY}',
            }
            _req.delete(url, headers=headers, timeout=10)
        except Exception:
            pass
    return True
