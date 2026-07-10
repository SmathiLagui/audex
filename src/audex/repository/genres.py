import sqlite3

from ..models import GenreRow
from .common import find_or_create_by_name, preload_name_cache


def preload_genres(conn: sqlite3.Connection) -> dict[str, int]:
    return preload_name_cache(conn, 'genres')


def find_or_create_genre(
    conn: sqlite3.Connection,
    name: str,
    cache: dict[str, int],
) -> int:
    return find_or_create_by_name(conn, 'genres', name, 'Unknown', cache)


def get_all_genres(conn: sqlite3.Connection) -> list[GenreRow]:
    rows = conn.execute('SELECT id, name FROM genres ORDER BY name')
    return GenreRow.from_db_rows(rows)
