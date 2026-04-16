"""
One-time migration: SQLite → Supabase
Run once: python3 migrate_to_supabase.py
"""
import sqlite3, os, sys, logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger()

SQLITE_PATH = os.path.join(os.path.dirname(__file__), 'database.db')

# Import db adapter (uses Supabase if configured)
from db_supabase import get_db, SUPABASE_URL, SUPABASE_KEY

if not SUPABASE_URL or not SUPABASE_KEY:
    log.error("❌ SUPABASE_URL / SUPABASE_SECRET_KEY not set")
    sys.exit(1)

log.info(f"🔗 Migrating SQLite → Supabase: {SUPABASE_URL}")

# ── Read from SQLite ──────────────────────────────────────────────────────────
src = sqlite3.connect(SQLITE_PATH)
src.row_factory = sqlite3.Row

# ── Connect to Supabase ───────────────────────────────────────────────────────
dst = get_db()

TABLES_IN_ORDER = [
    'app_settings',
    'users',
    'learning_materials',
    'user_points',
    'user_health_stats',
    'questionnaires',
    'post_test_status',
    'exercises',
    'daily_logs',
    'challenges',
    'notifications',
    'custom_questions',
    'lab_results',
    'certificates',
    'social_shares',
    'video_watches',
]

total_inserted = 0

for table in TABLES_IN_ORDER:
    try:
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
    except Exception as e:
        log.warning(f"  ⚠️  {table}: skip (SQLite error: {e})")
        continue

    if not rows:
        log.info(f"  ⬜ {table}: 0 rows (empty)")
        continue

    cols = rows[0].keys()
    col_str = ', '.join(cols)
    inserted = 0
    skipped = 0

    for row in rows:
        vals = [row[c] for c in cols]
        placeholders = ', '.join(['?' for _ in cols])

        # Build INSERT — use INSERT OR IGNORE equivalent
        sql = f"INSERT OR IGNORE INTO {table} ({col_str}) VALUES ({placeholders})"

        try:
            dst.execute(sql, tuple(vals))
            dst.commit()
            inserted += 1
        except Exception as e:
            err = str(e)
            if 'duplicate' in err.lower() or 'unique' in err.lower() or 'conflict' in err.lower():
                skipped += 1
            else:
                log.warning(f"    ⚠️  {table} row error: {err[:120]}")
                skipped += 1

    total_inserted += inserted
    log.info(f"  ✅ {table}: {inserted} inserted, {skipped} skipped")

src.close()
log.info(f"\n🎉 Migration done — {total_inserted} total rows → Supabase")
