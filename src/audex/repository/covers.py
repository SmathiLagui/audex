import sqlite3


def get_all_cover_hashes(conn: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        row[0] for row in conn.execute('SELECT content_hash FROM covers')
    )


def find_or_create_cover(
    conn: sqlite3.Connection,
    content_hash: str,
    extension: str,
) -> int:
    row = conn.execute(
        'SELECT id FROM covers WHERE content_hash = ?', (content_hash,)
    ).fetchone()
    if row:
        return int(row['id'])
    cur = conn.execute(
        'INSERT INTO covers (content_hash, extension) VALUES (?, ?)',
        (content_hash, extension),
    )
    return cur.lastrowid  # type: ignore[return-value]
