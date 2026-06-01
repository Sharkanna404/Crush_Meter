"""
心动解码器 - 数据库模块（含用户系统）
SQLite 存储分析历史与用户信息
"""

import sqlite3
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "heart_decoder.db"


def get_conn():
    return sqlite3.connect(str(DB_PATH))


def init_db():
    conn = get_conn()

    # users 表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # analyses 表（含用户隔离字段）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            guest_uid TEXT,
            crush_name TEXT NOT NULL,
            chat_preview TEXT NOT NULL,
            heart_rate INTEGER NOT NULL,
            level TEXT NOT NULL,
            dimensions TEXT NOT NULL,
            key_signals TEXT NOT NULL,
            advice TEXT NOT NULL,
            risk_warning TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # 检查并添加新字段（兼容旧版本）
    _migrate_add_columns(conn)

    # images 表（OCR 图片管理）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            guest_uid TEXT,
            filename TEXT NOT NULL,
            original_name TEXT NOT NULL,
            ocr_text TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            sort_order INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    conn.commit()
    conn.close()


def _migrate_add_columns(conn):
    """兼容旧数据库：如果 analyses 表缺少 user_id 或 guest_uid，则自动添加"""
    cursor = conn.execute("PRAGMA table_info(analyses)")
    columns = {row[1] for row in cursor.fetchall()}

    if "user_id" not in columns:
        conn.execute("ALTER TABLE analyses ADD COLUMN user_id INTEGER")
    if "guest_uid" not in columns:
        conn.execute("ALTER TABLE analyses ADD COLUMN guest_uid TEXT")
        # 给旧数据一个默认 guest_uid，便于后续迁移
        conn.execute("UPDATE analyses SET guest_uid = 'legacy' WHERE guest_uid IS NULL")


def generate_guest_uid() -> str:
    """生成临时用户 UID"""
    return "guest_" + secrets.token_urlsafe(16)


# ---- 用户相关 ----

def create_user(username: str, password_hash: str) -> int:
    conn = get_conn()
    try:
        cursor = conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, password_hash, datetime.now().isoformat())
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError("用户名已存在")
    finally:
        conn.close()


def get_user_by_username(username: str) -> dict:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def get_user_by_id(user_id: int) -> dict:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def migrate_guest_to_user(guest_uid: str, user_id: int) -> int:
    """将临时用户的分析记录迁移到注册用户"""
    conn = get_conn()
    cursor = conn.execute(
        "UPDATE analyses SET user_id = ?, guest_uid = NULL WHERE guest_uid = ? AND user_id IS NULL",
        (user_id, guest_uid)
    )
    conn.commit()
    migrated = cursor.rowcount
    conn.close()
    return migrated


# ---- 分析相关 ----

def save_analysis(
    crush_name: str,
    chat_preview: str,
    heart_rate: int,
    level: str,
    dimensions: dict,
    key_signals: list,
    advice: str,
    user_id: int = None,
    guest_uid: str = None,
    risk_warning: str = ""
) -> int:
    conn = get_conn()
    cursor = conn.execute(
        """
        INSERT INTO analyses (user_id, guest_uid, crush_name, chat_preview, heart_rate, level, dimensions, key_signals, advice, risk_warning, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            guest_uid,
            crush_name,
            chat_preview[:500],
            heart_rate,
            level,
            json.dumps(dimensions, ensure_ascii=False),
            json.dumps(key_signals, ensure_ascii=False),
            advice,
            risk_warning,
            datetime.now().isoformat()
        )
    )
    conn.commit()
    analysis_id = cursor.lastrowid
    conn.close()
    return analysis_id


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "crush_name": row["crush_name"],
        "chat_preview": row["chat_preview"],
        "heart_rate": row["heart_rate"],
        "level": row["level"],
        "dimensions": json.loads(row["dimensions"]),
        "key_signals": json.loads(row["key_signals"]),
        "advice": row["advice"],
        "risk_warning": row["risk_warning"],
        "created_at": row["created_at"]
    }


def get_history(user_id: int = None, guest_uid: str = None, limit: int = 20) -> list:
    conn = get_conn()
    conn.row_factory = sqlite3.Row

    if user_id:
        rows = conn.execute(
            "SELECT * FROM analyses WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    elif guest_uid:
        rows = conn.execute(
            "SELECT * FROM analyses WHERE guest_uid = ? ORDER BY created_at DESC LIMIT ?",
            (guest_uid, limit)
        ).fetchall()
    else:
        rows = []

    conn.close()
    return [_row_to_dict(row) for row in rows]


def get_trend(crush_name: str, user_id: int = None, guest_uid: str = None) -> list:
    conn = get_conn()
    conn.row_factory = sqlite3.Row

    if user_id:
        rows = conn.execute(
            """SELECT id, heart_rate, level, created_at FROM analyses
               WHERE crush_name = ? AND user_id = ? ORDER BY created_at ASC""",
            (crush_name, user_id)
        ).fetchall()
    elif guest_uid:
        rows = conn.execute(
            """SELECT id, heart_rate, level, created_at FROM analyses
               WHERE crush_name = ? AND guest_uid = ? ORDER BY created_at ASC""",
            (crush_name, guest_uid)
        ).fetchall()
    else:
        rows = []

    conn.close()
    return [
        {"id": row["id"], "heart_rate": row["heart_rate"], "level": row["level"], "created_at": row["created_at"]}
        for row in rows
    ]


def get_analysis_by_id(analysis_id: int) -> dict:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM analyses WHERE id = ?",
        (analysis_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)


# ---- 图片/OCR 相关 ----

def save_image(filename: str, original_name: str, user_id: int = None, guest_uid: str = None) -> int:
    conn = get_conn()
    cursor = conn.execute(
        """
        INSERT INTO images (user_id, guest_uid, filename, original_name, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, guest_uid, filename, original_name, 'pending', datetime.now().isoformat())
    )
    conn.commit()
    image_id = cursor.lastrowid
    conn.close()
    return image_id


def get_images(user_id: int = None, guest_uid: str = None) -> list:
    conn = get_conn()
    conn.row_factory = sqlite3.Row

    if user_id:
        rows = conn.execute(
            "SELECT * FROM images WHERE user_id = ? ORDER BY sort_order, created_at",
            (user_id,)
        ).fetchall()
    elif guest_uid:
        rows = conn.execute(
            "SELECT * FROM images WHERE guest_uid = ? ORDER BY sort_order, created_at",
            (guest_uid,)
        ).fetchall()
    else:
        rows = []

    conn.close()
    return [
        {
            "id": row["id"],
            "filename": row["filename"],
            "original_name": row["original_name"],
            "ocr_text": row["ocr_text"],
            "status": row["status"],
            "sort_order": row["sort_order"],
            "created_at": row["created_at"]
        }
        for row in rows
    ]


def get_image_by_id(image_id: int) -> dict:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM images WHERE id = ?", (image_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row["id"],
        "filename": row["filename"],
        "original_name": row["original_name"],
        "ocr_text": row["ocr_text"],
        "status": row["status"],
        "user_id": row["user_id"],
        "guest_uid": row["guest_uid"],
    }


def delete_image(image_id: int) -> bool:
    conn = get_conn()
    cursor = conn.execute("DELETE FROM images WHERE id = ?", (image_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def update_image_ocr(image_id: int, ocr_text: str, status: str = 'done') -> bool:
    conn = get_conn()
    cursor = conn.execute(
        "UPDATE images SET ocr_text = ?, status = ? WHERE id = ?",
        (ocr_text, status, image_id)
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def update_image_order(image_id: int, sort_order: int) -> bool:
    conn = get_conn()
    cursor = conn.execute(
        "UPDATE images SET sort_order = ? WHERE id = ?",
        (sort_order, image_id)
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def clear_user_images(user_id: int = None, guest_uid: str = None) -> int:
    """清空用户的所有图片记录"""
    conn = get_conn()
    if user_id:
        cursor = conn.execute("DELETE FROM images WHERE user_id = ?", (user_id,))
    elif guest_uid:
        cursor = conn.execute("DELETE FROM images WHERE guest_uid = ?", (guest_uid,))
    else:
        conn.close()
        return 0
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    return deleted
