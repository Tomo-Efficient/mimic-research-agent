"""SQLite history database for workflow runs."""
import json
import sqlite3
import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "history.db"


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


def save_eda_cache(data_dir: str, result: dict):
    """Cache EDA result keyed by data directory path."""
    with _conn() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS eda_cache (
                data_dir TEXT PRIMARY KEY,
                result TEXT NOT NULL
            )
        """)
        db.execute(
            "INSERT OR REPLACE INTO eda_cache (data_dir, result) VALUES (?, ?)",
            (data_dir, json.dumps(result, ensure_ascii=False, default=str)),
        )
        db.commit()


def get_eda_cache(data_dir: str) -> dict | None:
    """Retrieve cached EDA result, or None."""
    with _conn() as db:
        db.execute("CREATE TABLE IF NOT EXISTS eda_cache (data_dir TEXT PRIMARY KEY, result TEXT NOT NULL)")
        row = db.execute("SELECT result FROM eda_cache WHERE data_dir = ?", (data_dir,)).fetchone()
        if row:
            try:
                return json.loads(row["result"])
            except (json.JSONDecodeError, TypeError):
                return None
    return None


def save_ideas_pool(ideas: list):
    """Replace the entire ideas pool with a new list."""
    with _conn() as db:
        db.execute("CREATE TABLE IF NOT EXISTS ideas_pool (id INTEGER PRIMARY KEY AUTOINCREMENT, idea_json TEXT NOT NULL)")
        db.execute("DELETE FROM ideas_pool")
        for idea in ideas:
            db.execute("INSERT INTO ideas_pool (idea_json) VALUES (?)", (json.dumps(idea, ensure_ascii=False, default=str),))
        db.commit()


def get_ideas_pool() -> list[dict]:
    """Retrieve all cached ideas."""
    with _conn() as db:
        db.execute("CREATE TABLE IF NOT EXISTS ideas_pool (id INTEGER PRIMARY KEY AUTOINCREMENT, idea_json TEXT NOT NULL)")
        rows = db.execute("SELECT idea_json FROM ideas_pool ORDER BY id").fetchall()
        ideas = []
        for row in rows:
            try:
                ideas.append(json.loads(row["idea_json"]))
            except (json.JSONDecodeError, TypeError):
                pass
        return ideas


# Initialize on import
init_db()
