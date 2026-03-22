import sqlite3
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id     INTEGER PRIMARY KEY,
                    goal        TEXT,
                    deadline    TEXT,
                    notifs_per_day INTEGER DEFAULT 1,
                    streak      INTEGER DEFAULT 0,
                    last_done   TEXT,
                    created_at  TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER,
                    task_date   TEXT,
                    text        TEXT,
                    done        INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );
            """)

    def ensure_user(self, user_id: int):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
                (user_id,)
            )

    def save_goal(self, user_id: int, goal: str, deadline: datetime, notifs_per_day: int):
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO users (user_id, goal, deadline, notifs_per_day, streak)
                VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(user_id) DO UPDATE SET
                    goal = excluded.goal,
                    deadline = excluded.deadline,
                    notifs_per_day = excluded.notifs_per_day,
                    streak = 0,
                    last_done = NULL
            """, (user_id, goal, deadline.isoformat(), notifs_per_day))

    def get_user_data(self, user_id: int) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("deadline"):
            try:
                d["deadline"] = datetime.fromisoformat(d["deadline"])
            except Exception:
                pass
        return d

    def get_all_active_users(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE goal IS NOT NULL"
            ).fetchall()
        return [dict(r) for r in rows]

    def add_today_task(self, user_id: int, text: str):
        today = date.today().isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO tasks (user_id, task_date, text) VALUES (?, ?, ?)",
                (user_id, today, text)
            )

    def get_today_tasks(self, user_id: int) -> List[Dict[str, Any]]:
        today = date.today().isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE user_id = ? AND task_date = ?",
                (user_id, today)
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_today_done(self, user_id: int):
        today = date.today().isoformat()

        with self._conn() as conn:
            user = conn.execute(
                "SELECT streak, last_done FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()

            if not user:
                return

            last_done = user["last_done"]
            streak = user["streak"] or 0

            # Only increment if not already marked done today
            if last_done != today:
                # Check if yesterday was done (to maintain streak)
                yesterday = (date.today() - timedelta(days=1)).isoformat()
                if last_done == yesterday or streak == 0:
                    streak += 1
                else:
                    # Missed a day, start fresh
                    streak = 1

                conn.execute(
                    "UPDATE users SET streak = ?, last_done = ? WHERE user_id = ?",
                    (streak, today, user_id)
                )

            # Mark all today's tasks as done
            conn.execute(
                "UPDATE tasks SET done = 1 WHERE user_id = ? AND task_date = ?",
                (user_id, today)
            )

    def reset_streak(self, user_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET streak = 0 WHERE user_id = ?",
                (user_id,)
            )

    def delete_user(self, user_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))