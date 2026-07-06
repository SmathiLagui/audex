import sqlite3

from ..models import AlbumQueryRow


def find_or_create_album(
    conn: sqlite3.Connection,
    *,
    title: str,
    artist_id: int,
    year: int | None,
    genre_id: int,
    cover_id: int | None,
) -> int:
    title = title.strip() or 'Unknown Album'
    row = conn.execute(
        'SELECT id FROM albums WHERE title = ? AND artist_id = ?',
        (title, artist_id),
    ).fetchone()
    if row:
        return int(row['id'])
    cur = conn.execute(
        'INSERT INTO albums (title, artist_id, year, genre_id, cover_id)'
        ' VALUES (?, ?, ?, ?, ?)',
        (title, artist_id, year, genre_id, cover_id),
    )
    return cur.lastrowid  # type: ignore[return-value]


def update_album_cover(
    conn: sqlite3.Connection,
    album_id: int,
    cover_id: int | None,
) -> None:
    conn.execute(
        'UPDATE albums SET cover_id = ? WHERE id = ?',
        (cover_id, album_id),
    )


def update_compilation_flags(
    conn: sqlite3.Connection,
    album_ids: frozenset[int],
) -> None:
    if not album_ids:
        return
    placeholders = ','.join('?' * len(album_ids))
    conn.execute(
        f"""
        UPDATE albums
        SET is_compilation = (
            SELECT COUNT(DISTINCT artist_id) > 1
            FROM tracks
            WHERE album_id = albums.id
        )
        WHERE id IN ({placeholders})
        """,
        tuple(album_ids),
    )


def get_album_rows(conn: sqlite3.Connection) -> list[AlbumQueryRow]:
    rows = conn.execute(
        """
            SELECT
                a.id,
                a.title,
                a.artist_id,
                a.year,
                a.genre_id,
                a.is_compilation,
                c.content_hash,
                c.extension,
                COUNT(t.id) AS track_count
            FROM albums a
            LEFT JOIN covers c ON c.id = a.cover_id
            LEFT JOIN tracks t ON t.album_id = a.id
            GROUP BY a.id
            ORDER BY a.id
            """
    )
    return AlbumQueryRow.from_db_rows(rows)


def get_track_ids_by_album(conn: sqlite3.Connection) -> dict[int, list[int]]:
    result: dict[int, list[int]] = {}
    for row in conn.execute(
        'SELECT album_id, id FROM tracks'
        ' ORDER BY album_id, disc_number NULLS LAST, track_number NULLS LAST'
    ):
        track_ids_for_album = result.setdefault(row['album_id'], [])
        track_ids_for_album.append(row['id'])
    return result
