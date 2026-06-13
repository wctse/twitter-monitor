import os
import sqlite3


_DB_PATH = os.path.join(os.path.dirname(__file__), "data", "posts.db")


def _connect(db_path: str = _DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = _DB_PATH):
    conn = _connect(db_path)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            post_id TEXT NOT NULL,
            post_url TEXT NOT NULL,
            post_title TEXT,
            published_at TEXT,
            content_chars INTEGER DEFAULT 0,
            skip_reason TEXT,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_id, post_id)
        );
        CREATE TABLE IF NOT EXISTS post_attempts (
            source_id TEXT NOT NULL,
            post_id TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_id, post_id)
        );
        CREATE INDEX IF NOT EXISTS idx_posts_source ON posts(source_id);
        CREATE INDEX IF NOT EXISTS idx_posts_published ON posts(published_at);
        """
    )
    conn.commit()
    conn.close()


def get_attempts(source_id: str, post_id: str, db_path: str = _DB_PATH) -> int:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT attempts FROM post_attempts WHERE source_id = ? AND post_id = ?",
            (source_id, post_id),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def bump_attempts(
    source_id: str,
    post_id: str,
    last_error: str | None = None,
    db_path: str = _DB_PATH,
) -> int:
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO post_attempts (source_id, post_id, attempts, last_error)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(source_id, post_id) DO UPDATE SET
                attempts = attempts + 1,
                last_error = excluded.last_error,
                updated_at = CURRENT_TIMESTAMP
            """,
            (source_id, post_id, last_error),
        )
        conn.commit()
        row = conn.execute(
            "SELECT attempts FROM post_attempts WHERE source_id = ? AND post_id = ?",
            (source_id, post_id),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def has_any_posts(source_id: str, db_path: str = _DB_PATH) -> bool:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM posts WHERE source_id = ? LIMIT 1", (source_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def is_processed(source_id: str, post_id: str, db_path: str = _DB_PATH) -> bool:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM posts WHERE source_id = ? AND post_id = ? LIMIT 1",
            (source_id, post_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_processed(
    source_id: str,
    post_id: str,
    post_url: str,
    post_title: str,
    published_at: str | None,
    content_chars: int,
    skip_reason: str | None = None,
    db_path: str = _DB_PATH,
):
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO posts (source_id, post_id, post_url, post_title, published_at, content_chars, skip_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, post_id) DO UPDATE SET
                post_url = excluded.post_url,
                post_title = excluded.post_title,
                published_at = excluded.published_at,
                content_chars = excluded.content_chars,
                skip_reason = excluded.skip_reason,
                processed_at = CURRENT_TIMESTAMP
            """,
            (source_id, post_id, post_url, post_title, published_at, content_chars, skip_reason),
        )
        conn.commit()
    finally:
        conn.close()
