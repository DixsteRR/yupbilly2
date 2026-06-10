import sqlite3
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_PATH = "bot.db"

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id             INTEGER PRIMARY KEY,
                username            TEXT DEFAULT '',
                first_name          TEXT DEFAULT '',
                created_at          TEXT NOT NULL,
                subscription_until  TEXT,
                trial_until         TEXT,
                trial_used          INTEGER DEFAULT 0,
                free_messages_used  INTEGER DEFAULT 0,
                total_messages      INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                created_at  TEXT NOT NULL
            );
        """)
    print("✅ База данных инициализирована")

def create_user_if_not_exists(user_id: int, username: str, first_name: str):
    with get_conn() as conn:
        existing = conn.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO users (user_id, username, first_name, created_at) VALUES (?,?,?,?)",
                (user_id, username, first_name, datetime.now().isoformat())
            )

def get_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

def increment_message_count(user_id: int, is_free: bool = False):
    with get_conn() as conn:
        if is_free:
            conn.execute(
                "UPDATE users SET total_messages=total_messages+1, free_messages_used=free_messages_used+1 WHERE user_id=?",
                (user_id,)
            )
        else:
            conn.execute(
                "UPDATE users SET total_messages=total_messages+1 WHERE user_id=?",
                (user_id,)
            )
        conn.execute(
            "INSERT INTO messages (user_id, created_at) VALUES (?,?)",
            (user_id, datetime.now().isoformat())
        )

def activate_trial(user_id: int, until_iso: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET trial_until=?, trial_used=1 WHERE user_id=?",
            (until_iso, user_id)
        )

def activate_subscription(user_id: int, until_iso: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET subscription_until=? WHERE user_id=?",
            (until_iso, user_id)
        )

def get_all_user_ids() -> list[int]:
    with get_conn() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
        return [r["user_id"] for r in rows]

def get_stats() -> dict:
    with get_conn() as conn:
        now = datetime.now().isoformat()
        today = datetime.now().date().isoformat()
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()

        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        active_subs = conn.execute(
            "SELECT COUNT(*) FROM users WHERE subscription_until > ?", (now,)
        ).fetchone()[0]
        active_trials = conn.execute(
            "SELECT COUNT(*) FROM users WHERE trial_until > ? AND (subscription_until IS NULL OR subscription_until < ?)",
            (now, now)
        ).fetchone()[0]
        messages_today = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE created_at >= ?", (today,)
        ).fetchone()[0]
        new_users_week = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= ?", (week_ago,)
        ).fetchone()[0]

        return {
            "total_users": total_users,
            "active_subs": active_subs,
            "active_trials": active_trials,
            "messages_today": messages_today,
            "new_users_week": new_users_week,
        }
