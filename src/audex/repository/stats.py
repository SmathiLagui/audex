import sqlite3

from ..models import ExportStats


def query_stats(conn: sqlite3.Connection) -> ExportStats:
    row = conn.execute(
        """
        SELECT
            COUNT(DISTINCT t.id)  AS track_count,
            COUNT(DISTINCT a.id)  AS album_count,
            COUNT(DISTINCT ar.id) AS artist_count,
            COUNT(DISTINCT g.id)  AS genre_count,
            COALESCE(SUM(t.duration_ms), 0) AS total_duration_ms
        FROM tracks t
        LEFT JOIN albums a   ON a.id  = t.album_id
        LEFT JOIN artists ar ON ar.id = t.artist_id
        LEFT JOIN genres g   ON g.id  = a.genre_id
        """
    ).fetchone()
    return ExportStats.from_db(row)
