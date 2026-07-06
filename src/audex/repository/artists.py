import sqlite3

from ..models import ArtistRow
from .common import find_or_create_by_name


def find_or_create_artist(conn: sqlite3.Connection, name: str) -> int:
    return find_or_create_by_name(conn, 'artists', name, 'Unknown Artist')


def get_all_artists(conn: sqlite3.Connection) -> list[ArtistRow]:
    rows = conn.execute('SELECT id, name FROM artists ORDER BY name')
    return ArtistRow.from_db_rows(rows)
