import sqlite3

from ..models import ExportStats


def query_stats(conn: sqlite3.Connection) -> ExportStats:
    # Independent COUNT(*) per table - joining through tracks would
    # undercount e.g. a compilation's album artist ("Various Artists") when
    # that artist never appears as an individual track's artist_id.
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM tracks)  AS track_count,
            (SELECT COUNT(*) FROM albums)  AS album_count,
            (SELECT COUNT(*) FROM artists) AS artist_count,
            (SELECT COUNT(*) FROM genres)  AS genre_count,
            (SELECT COALESCE(SUM(duration_ms), 0) FROM tracks)
                AS total_duration_ms
        """
    ).fetchone()
    return ExportStats.from_db(row)
