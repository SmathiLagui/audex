import sqlite3


def preload_name_cache(
    conn: sqlite3.Connection,
    table: str,
) -> dict[str, int]:
    return {
        row['name'].casefold(): row['id']
        for row in conn.execute(f'SELECT id, name FROM {table}')
    }


def find_or_create_by_name(
    conn: sqlite3.Connection,
    table: str,
    name: str,
    default: str,
    cache: dict[str, int],
) -> int:
    name = name.strip() or default
    key = name.casefold()
    cached_id = cache.get(key)
    if cached_id is not None:
        return cached_id
    try:
        cur = conn.execute(f'INSERT INTO {table} (name) VALUES (?)', (name,))
        if cur.lastrowid is None:
            raise RuntimeError(f'INSERT into {table} returned no rowid')
        row_id = cur.lastrowid
    except sqlite3.IntegrityError:
        row = conn.execute(
            f'SELECT id FROM {table} WHERE name = ?', (name,)
        ).fetchone()
        row_id = int(row['id'])
    cache[key] = row_id
    return row_id
