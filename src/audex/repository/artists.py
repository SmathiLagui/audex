import sqlite3

from ..models import ArtistRow
from .common import find_or_create_by_name, preload_name_cache


def preload_artists(conn: sqlite3.Connection) -> dict[str, int]:
    return preload_name_cache(conn, 'artists')


def find_or_create_artist(
    conn: sqlite3.Connection,
    name: str,
    cache: dict[str, int],
) -> int:
    return find_or_create_by_name(
        conn,
        'artists',
        name,
        'Unknown Artist',
        cache,
    )


def get_all_artists(conn: sqlite3.Connection) -> list[ArtistRow]:
    rows = conn.execute('SELECT id, name FROM artists ORDER BY name')
    return ArtistRow.from_db_rows(rows)
