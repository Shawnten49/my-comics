from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List

try:
    from .database import Database
except ImportError:
    from database import Database


class FavoriteStore:
    def __init__(self, database_path: Path) -> None:
        self.database = Database(database_path)

    def is_favorite(self, book_id: str) -> bool:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM favorites WHERE book_id = ?",
                (book_id,),
            ).fetchone() is not None

    def added_at(self, book_id: str) -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT added_at FROM favorites WHERE book_id = ?",
                (book_id,),
            ).fetchone()
        return row["added_at"] if row else ""

    def count(self) -> int:
        with self.database.connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM favorites"
            ).fetchone()[0])

    def set_favorite(self, book_id: str, favorite: bool) -> bool:
        with self.database.connect() as connection:
            if favorite:
                connection.execute(
                    """
                    INSERT INTO favorites(book_id, added_at) VALUES (?, ?)
                    ON CONFLICT(book_id) DO NOTHING
                    """,
                    (
                        book_id,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
            else:
                connection.execute(
                    "DELETE FROM favorites WHERE book_id = ?",
                    (book_id,),
                )
            connection.commit()
        return favorite

    def entries(self) -> List[tuple]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT book_id, added_at FROM favorites
                ORDER BY added_at DESC, book_id DESC
                """
            ).fetchall()
        return [(row["book_id"], row["added_at"]) for row in rows]

    def prune(self, valid_ids: set) -> int:
        if not valid_ids:
            return 0
        with self.database.connect() as connection:
            placeholders = ",".join("?" for _ in valid_ids)
            cursor = connection.execute(
                f"DELETE FROM favorites WHERE book_id NOT IN ({placeholders})",
                tuple(valid_ids),
            )
            connection.commit()
            return cursor.rowcount
