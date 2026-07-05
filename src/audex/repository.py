import sqlite3

from .models import FileStateRow

# ---------------------------------------------------------------------------
# Find-or-create helpers
# ---------------------------------------------------------------------------


def find_or_create_genre(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip() or 'Unknown'
    row = conn.execute(
        'SELECT id FROM genres WHERE name = ?', (name,)
    ).fetchone()
    if row:
        return int(row['id'])
    cur = conn.execute('INSERT INTO genres (name) VALUES (?)', (name,))
    return cur.lastrowid  # type: ignore[return-value]


def find_or_create_artist(conn: sqlite3.Connection, name: str) -> int:
    name = name.strip() or 'Unknown Artist'
    row = conn.execute(
        'SELECT id FROM artists WHERE name = ?', (name,)
    ).fetchone()
    if row:
        return int(row['id'])
    cur = conn.execute('INSERT INTO artists (name) VALUES (?)', (name,))
    return cur.lastrowid  # type: ignore[return-value]


def find_or_create_cover(
    conn: sqlite3.Connection,
    sha256_hash: str,
    extension: str,
) -> int:
    row = conn.execute(
        'SELECT id FROM covers WHERE sha256_hash = ?', (sha256_hash,)
    ).fetchone()
    if row:
        return int(row['id'])
    cur = conn.execute(
        'INSERT INTO covers (sha256_hash, extension) VALUES (?, ?)',
        (sha256_hash, extension),
    )
    return cur.lastrowid  # type: ignore[return-value]


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
    """Mark albums as compilations when their tracks have multiple artists."""
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


# ---------------------------------------------------------------------------
# Track upsert
# ---------------------------------------------------------------------------


def upsert_track(
    conn: sqlite3.Connection,
    *,
    title: str | None,
    artist_id: int,
    album_id: int,
    track_number: int | None,
    disc_number: int | None,
    duration_ms: int,
    path: str,
    has_cover: bool,
    bitrate_kbps: int | None,
    audio_format: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO tracks (
            title, artist_id, album_id,
            track_number, disc_number, duration_ms, path, has_cover,
            bitrate_kbps, audio_format
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            title        = excluded.title,
            artist_id    = excluded.artist_id,
            album_id     = excluded.album_id,
            track_number = excluded.track_number,
            disc_number  = excluded.disc_number,
            duration_ms  = excluded.duration_ms,
            has_cover    = excluded.has_cover,
            bitrate_kbps = excluded.bitrate_kbps,
            audio_format = excluded.audio_format
        """,
        (
            title,
            artist_id,
            album_id,
            track_number,
            disc_number,
            duration_ms,
            path,
            1 if has_cover else 0,
            bitrate_kbps,
            audio_format,
        ),
    )


# ---------------------------------------------------------------------------
# File state
# ---------------------------------------------------------------------------


def upsert_file_state(conn: sqlite3.Connection, state: FileStateRow) -> None:
    conn.execute(
        """
        INSERT INTO file_states (path, size_bytes, change_time_ns)
        VALUES (?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            size_bytes     = excluded.size_bytes,
            change_time_ns = excluded.change_time_ns
        """,
        (state.path, state.size_bytes, state.change_time_ns),
    )


def get_all_file_states(conn: sqlite3.Connection) -> dict[str, FileStateRow]:
    rows = conn.execute(
        'SELECT path, size_bytes, change_time_ns FROM file_states'
    ).fetchall()
    return {
        row['path']: FileStateRow.model_validate(dict(row)) for row in rows
    }


def delete_by_path(conn: sqlite3.Connection, path: str) -> None:
    conn.execute('DELETE FROM file_states WHERE path = ?', (path,))
    conn.execute('DELETE FROM tracks WHERE path = ?', (path,))


# ---------------------------------------------------------------------------
# Orphan cleanup
# ---------------------------------------------------------------------------


def cleanup_orphans(conn: sqlite3.Connection) -> None:
    conn.execute(
        'DELETE FROM albums'
        ' WHERE id NOT IN (SELECT DISTINCT album_id FROM tracks)'
    )
    conn.execute(
        """
        DELETE FROM artists
        WHERE id NOT IN (SELECT DISTINCT artist_id FROM tracks)
          AND id NOT IN (SELECT DISTINCT artist_id FROM albums)
        """
    )
    conn.execute(
        'DELETE FROM genres'
        ' WHERE id NOT IN (SELECT DISTINCT genre_id FROM albums)'
    )
    conn.execute(
        """
        DELETE FROM covers
        WHERE id NOT IN (
            SELECT DISTINCT cover_id FROM albums WHERE cover_id IS NOT NULL
        )
        """
    )
