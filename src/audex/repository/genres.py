import sqlite3

from ..models import GenreRow
from .common import find_or_create_by_name


def find_or_create_genre(conn: sqlite3.Connection, name: str) -> int:
    return find_or_create_by_name(conn, 'genres', name, 'Unknown')


def get_all_genres(conn: sqlite3.Connection) -> list[GenreRow]:
    rows = conn.execute('SELECT id, name FROM genres ORDER BY name')
    return GenreRow.from_db_rows(rows)
