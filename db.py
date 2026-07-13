"""
Cache & history storage. Upstash Redis REST API (production) with SQLite fallback (local dev).
Set REDIS_REST_URL and REDIS_REST_TOKEN env vars for persistent cache on Render.
"""

import json
import os
import sqlite3
import datetime
import urllib.request
import urllib.error
from pathlib import Path

DB_PATH = Path(__file__).parent / "history.db"

# ---- Upstash Redis REST API (production cache) ----
REST_URL = os.environ.get("REDIS_REST_URL", "").rstrip("/")
REST_TOKEN = os.environ.get("REDIS_REST_TOKEN")
_rest_ok = False


def _redis_rest(cmd: str, key: str, value: str | None = None) -> str | None:
    """Call Upstash Redis REST API. Returns response body or None."""
    url = f"{REST_URL}/{cmd}/{key}"
    req = urllib.request.Request(url, method="GET" if value is None else "POST")
    req.add_header("Authorization", f"Bearer {REST_TOKEN}")
    if value is not None:
        req.data = value.encode("utf-8")
        req.add_header("Content-Type", "text/plain")
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.read().decode("utf-8")
    except Exception:
        return None


if REST_URL and REST_TOKEN:
    r = _redis_rest("ping", "claude-test")
    if r is not None:
        _rest_ok = True
        print("[db] Upstash Redis REST connected — persistent cache enabled")
    else:
        print("[db] Upstash Redis REST unavailable, using SQLite cache")


def _cache_get(key: str) -> str | None:
    if _rest_ok:
        raw = _redis_rest("get", key)
        if raw:
            try:
                result = json.loads(raw).get("result")
                if result and result != "null":
                    return result
            except (json.JSONDecodeError, TypeError):
                pass
    return _sqlite_kv_get(key)


def _cache_set(key: str, value: str):
    if _rest_ok:
        _redis_rest("set", key, value)
        return
    _sqlite_kv_set(key, value)


def _cache_delete(key: str):
    if _rest_ok:
        _redis_rest("del", key)
        return
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
    """Cache EDA result. Key is path-independent so it works across machines."""
    val = json.dumps(result, ensure_ascii=False, default=str)
    _cache_set("eda_cache", val)


def get_eda_cache(data_dir: str) -> dict | None:
    """Retrieve cached EDA result, or None."""
    val = _cache_get("eda_cache")
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


# ---- Run history (Upstash REST primary, SQLite fallback) ----
RUNS_INDEX_KEY = "runs_index"
MAX_RUNS = 100


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
    """Save a complete workflow run. Persists across deploys with Upstash REST."""
    idea = session.selected_idea or {}
    idea_title = idea.get("title_cn") or idea.get("title", "")

    def to_json(obj):
        if obj is None:
            return "{}"
        if isinstance(obj, str):
            return obj
        return json.dumps(obj, ensure_ascii=False, default=str)

    now = datetime.datetime.now().isoformat()
    meta = {
        "id": run_id, "created_at": now, "mode": mode,
        "idea_title": idea_title, "data_dir": getattr(session, "data_dir", "") or "",
        "total_score": idea.get("total_score", 0) if isinstance(idea, dict) else 0,
    }
    data = {
        "skill1_output": to_json(session.skill1_output),
        "skill2_output": to_json(session.skill2_output),
        "selected_idea": to_json(session.selected_idea),
        "skill3_output": to_json(session.skill3_output),
        "skill4_output": to_json(session.skill4_output),
        "skill5_output": session.skill5_output or "",
    }
    full = {**meta, **data}

    if _rest_ok:
        try:
            run_json = json.dumps(full, ensure_ascii=False, default=str)
            _redis_rest("set", f"run:{run_id}", run_json)
            timestamp = int(datetime.datetime.now().timestamp() * 1000)
            # ZADD key score member
            _redis_rest("zadd", f"{RUNS_INDEX_KEY}/{timestamp}", run_id)
            # Trim: ZREMRANGEBYRANK key 0 (count - MAX_RUNS - 1)
            _redis_rest("zremrangebyrank", f"{RUNS_INDEX_KEY}/0/{max(0, 999)}", None)
            return
        except Exception:
            pass

    # SQLite fallback
    with _conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO runs (id, created_at, mode, idea_title, data_dir, total_score) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, now, mode, idea_title, getattr(session, "data_dir", "") or "", meta["total_score"]),
        )
        db.execute(
            "INSERT OR REPLACE INTO run_data (run_id, skill1_output, skill2_output, selected_idea, skill3_output, skill4_output, skill5_output) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, data["skill1_output"], data["skill2_output"], data["selected_idea"],
             data["skill3_output"], data["skill4_output"], data["skill5_output"]),
        )
        db.commit()


def list_runs(limit: int = 50) -> list[dict]:
    """List recent runs, newest first."""
    if _rest_ok:
        try:
            # ZRANGE key 0 limit-1 REV
            raw = _redis_rest("zrange", f"{RUNS_INDEX_KEY}/0/{limit - 1}/REV")
            if raw:
                ids = json.loads(raw).get("result", [])
                runs = []
                for rid in ids:
                    rraw = _redis_rest("get", f"run:{rid}")
                    if rraw:
                        rv = json.loads(rraw).get("result")
                        if rv:
                            meta = json.loads(rv)
                            runs.append({k: meta[k] for k in ("id", "created_at", "mode", "idea_title", "total_score") if k in meta})
                return runs
        except Exception:
            pass

    with _conn() as db:
        rows = db.execute(
            "SELECT id, created_at, mode, idea_title, total_score FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def load_run(run_id: str) -> dict | None:
    """Load a saved run's full data."""
    if _rest_ok:
        try:
            rraw = _redis_rest("get", f"run:{run_id}")
            if rraw:
                rv = json.loads(rraw).get("result")
                if rv:
                    full = json.loads(rv)
                    for key in ("skill1_output", "skill2_output", "selected_idea", "skill3_output", "skill4_output"):
                        try:
                            full[key] = json.loads(full.get(key, "{}"))
                        except (json.JSONDecodeError, TypeError):
                            full[key] = {}
                    return full
        except Exception:
            pass

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
    if _rest_ok:
        try:
            _redis_rest("del", f"run:{run_id}")
            _redis_rest("zrem", f"{RUNS_INDEX_KEY}", run_id)
            return
        except Exception:
            pass

    with _conn() as db:
        db.execute("DELETE FROM run_data WHERE run_id = ?", (run_id,))
        db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        db.commit()


# Initialize on import
init_db()
