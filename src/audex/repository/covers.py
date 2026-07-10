import sqlite3


def get_all_cover_hashes(conn: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        row[0] for row in conn.execute('SELECT content_hash FROM covers')
    )


def preload_covers(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row['content_hash']: row['id']
        for row in conn.execute('SELECT id, content_hash FROM covers')
    }


def find_or_create_cover(
    conn: sqlite3.Connection,
    content_hash: str,
    extension: str,
    cache: dict[str, int],
) -> int:
    cached_id = cache.get(content_hash)
    if cached_id is not None:
        return cached_id
    try:
        cur = conn.execute(
            'INSERT INTO covers (content_hash, extension) VALUES (?, ?)',
            (content_hash, extension),
        )
        if cur.lastrowid is None:
            raise RuntimeError('INSERT into covers returned no rowid')
        row_id = cur.lastrowid
    except sqlite3.IntegrityError:
        row = conn.execute(
            'SELECT id FROM covers WHERE content_hash = ?',
            (content_hash,),
        ).fetchone()
        row_id = int(row['id'])
    cache[content_hash] = row_id
    return row_id
