import sqlite3


def wipe_all(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute('DELETE FROM tracks')
        conn.execute('DELETE FROM file_states')
        conn.execute('DELETE FROM albums')
        conn.execute('DELETE FROM artists')
        conn.execute('DELETE FROM genres')
        conn.execute('DELETE FROM covers')
        conn.execute(
            'DELETE FROM sqlite_sequence'
            " WHERE name IN ('genres','artists','covers','albums','tracks')"
        )


def cleanup_orphans(conn: sqlite3.Connection) -> None:
    with conn:
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
