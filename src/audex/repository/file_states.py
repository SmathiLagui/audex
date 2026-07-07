import sqlite3

from ..models import FileStateRow


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


def count_tracked_files(conn: sqlite3.Connection) -> int:
    row = conn.execute('SELECT COUNT(*) FROM file_states').fetchone()[0]
    return int(row)


def get_all_file_states(conn: sqlite3.Connection) -> dict[str, FileStateRow]:
    rows = conn.execute(
        'SELECT path, size_bytes, change_time_ns FROM file_states'
    )
    file_state_rows = FileStateRow.from_db_rows(rows)
    return {r.path: r for r in file_state_rows}


def delete_by_path(conn: sqlite3.Connection, path: str) -> int | None:
    """Delete the file_state and track for *path*.

    Returns the deleted track's former album_id (or None if there was no
    track for this path), so the caller can recompute that album's
    is_compilation flag now that one of its tracks is gone.
    """
    with conn:
        row = conn.execute(
            'SELECT album_id FROM tracks WHERE path = ?', (path,)
        ).fetchone()
        conn.execute('DELETE FROM file_states WHERE path = ?', (path,))
        conn.execute('DELETE FROM tracks WHERE path = ?', (path,))
        return int(row['album_id']) if row else None
