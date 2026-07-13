"""
Cache & history storage. Redis (production) with SQLite fallback (local dev).
Set REDIS_URL env var to enable persistent Redis cache on Render.
"""

import json
import os
import sqlite3
import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "history.db"

# ---- Redis (production cache) ----
REDIS_URL = os.environ.get("REDIS_URL")
_redis = None
_redis_ok = False

if REDIS_URL:
    try:
        import redis as _redis_lib
        _redis = _redis_lib.from_url(REDIS_URL, socket_timeout=5, socket_connect_timeout=5)
        _redis.ping()
        _redis_ok = True
        print("[db] Redis connected — persistent cache enabled")
    except Exception as e:
        print(f"[db] Redis unavailable ({e}), using SQLite cache")


def _cache_get(key: str) -> str | None:
    if _redis_ok:
        try:
            val = _redis.get(key)
            return val.decode() if val else None
        except Exception:
            pass
    return _sqlite_kv_get(key)


def _cache_set(key: str, value: str):
    if _redis_ok:
        try:
            _redis.set(key, value)
            return
        except Exception:
            pass
    _sqlite_kv_set(key, value)


def _cache_delete(key: str):
    if _redis_ok:
        try:
            _redis.delete(key)
            return
        except Exception:
            pass
    _sqlite_kv_delete(key)


# ---- SQLite key-value fallback ----
def _sqlite_conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def _ensure_kv_table():
    with _sqlite_conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS kv_cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        db.commit()


def _sqlite_kv_get(key: str) -> str | None:
    _ensure_kv_table()
    with _sqlite_conn() as db:
        row = db.execute("SELECT value FROM kv_cache WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def _sqlite_kv_set(key: str, value: str):
    _ensure_kv_table()
    with _sqlite_conn() as db:
        db.execute("INSERT OR REPLACE INTO kv_cache (key, value) VALUES (?, ?)", (key, value))
        db.commit()


def _sqlite_kv_delete(key: str):
    _ensure_kv_table()
    with _sqlite_conn() as db:
        db.execute("DELETE FROM kv_cache WHERE key = ?", (key,))
        db.commit()


# ---- Public cache API ----
def save_eda_cache(data_dir: str, result: dict):
    """Cache EDA result keyed by data directory path. Persists across restarts."""
    key = f"eda:{data_dir}"
    _cache_set(key, json.dumps(result, ensure_ascii=False, default=str))


def get_eda_cache(data_dir: str) -> dict | None:
    """Retrieve cached EDA result, or None."""
    key = f"eda:{data_dir}"
    val = _cache_get(key)
    if val:
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def save_ideas_pool(ideas: list):
    """Replace the entire ideas pool with a new list. Persists across restarts."""
    _cache_set("ideas_pool", json.dumps(ideas, ensure_ascii=False, default=str))


def get_ideas_pool() -> list[dict]:
    """Retrieve all cached ideas."""
    val = _cache_get("ideas_pool")
    if val:
        try:
            ideas = json.loads(val)
            if isinstance(ideas, list):
                return ideas
        except (json.JSONDecodeError, TypeError):
            pass
    return []


# ---- Run history (SQLite only, non-critical) ----
def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def init_db():
    """Create tables if they don't exist."""
    with _conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                mode TEXT DEFAULT 'ai_assisted',
                idea_title TEXT DEFAULT '',
                data_dir TEXT DEFAULT '',
                total_score INTEGER DEFAULT 0
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS run_data (
                run_id TEXT PRIMARY KEY,
                skill1_output TEXT DEFAULT '{}',
                skill2_output TEXT DEFAULT '{}',
                selected_idea TEXT DEFAULT '{}',
                skill3_output TEXT DEFAULT '{}',
                skill4_output TEXT DEFAULT '{}',
                skill5_output TEXT DEFAULT '',
                FOREIGN KEY (run_id) REFERENCES runs(id)
            )
        """)
        db.commit()


def save_run(run_id: str, session, mode: str = "ai_assisted"):
    """Save a complete workflow run to the database."""
    idea = session.selected_idea or {}
    idea_title = idea.get("title_cn") or idea.get("title", "")
    total_score = idea.get("total_score", 0)

    def to_json(obj):
        if obj is None:
            return "{}"
        if isinstance(obj, str):
            return obj
        return json.dumps(obj, ensure_ascii=False, default=str)

    with _conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO runs (id, created_at, mode, idea_title, data_dir, total_score) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, datetime.datetime.now().isoformat(), mode, idea_title, session.data_dir or "", total_score),
        )
        db.execute(
            "INSERT OR REPLACE INTO run_data (run_id, skill1_output, skill2_output, selected_idea, skill3_output, skill4_output, skill5_output) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                to_json(session.skill1_output),
                to_json(session.skill2_output),
                to_json(session.selected_idea),
                to_json(session.skill3_output),
                to_json(session.skill4_output),
                session.skill5_output or "",
            ),
        )
        db.commit()


def list_runs(limit: int = 50) -> list[dict]:
    """List recent runs, newest first."""
    with _conn() as db:
        rows = db.execute(
            "SELECT id, created_at, mode, idea_title, total_score FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def load_run(run_id: str) -> dict | None:
    """Load a saved run's full data."""
    with _conn() as db:
        meta = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not meta:
            return None
        data = db.execute("SELECT * FROM run_data WHERE run_id = ?", (run_id,)).fetchone()
        result = dict(meta)
        if data:
            data_dict = dict(data)
            for key in ("skill1_output", "skill2_output", "selected_idea", "skill3_output", "skill4_output"):
                try:
                    data_dict[key] = json.loads(data_dict.get(key, "{}"))
                except (json.JSONDecodeError, TypeError):
                    data_dict[key] = {}
            result.update(data_dict)
        return result


def delete_run(run_id: str):
    """Delete a saved run."""
    with _conn() as db:
        db.execute("DELETE FROM run_data WHERE run_id = ?", (run_id,))
        db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        db.commit()


# Initialize on import
init_db()
