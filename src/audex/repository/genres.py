import sqlite3

from ..models import GenreRow


def find_or_create_genre(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip() or 'Unknown'
    row = conn.execute(
        'SELECT id FROM genres WHERE name = ?', (name,)
    ).fetchone()
    if row:
        return int(row['id'])
    cur = conn.execute('INSERT INTO genres (name) VALUES (?)', (name,))
    return cur.lastrowid  # type: ignore[return-value]


def get_all_genres(conn: sqlite3.Connection) -> list[GenreRow]:
    rows = conn.execute('SELECT id, name FROM genres ORDER BY name')
    return GenreRow.from_db_rows(rows)
