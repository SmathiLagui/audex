import sqlite3

from ..models import ArtistRow


def find_or_create_artist(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip() or 'Unknown Artist'
    row = conn.execute(
        'SELECT id FROM artists WHERE name = ?', (name,)
    ).fetchone()
    if row:
        return int(row['id'])
    cur = conn.execute('INSERT INTO artists (name) VALUES (?)', (name,))
    return cur.lastrowid  # type: ignore[return-value]


def get_all_artists(conn: sqlite3.Connection) -> list[ArtistRow]:
    rows = conn.execute('SELECT id, name FROM artists ORDER BY name')
    return ArtistRow.from_db_rows(rows)
