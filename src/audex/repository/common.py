import sqlite3


def find_or_create_by_name(
    conn: sqlite3.Connection,
    table: str,
    name: str,
    default: str,
) -> int:
    name = name.strip() or default
    row = conn.execute(
        f'SELECT id FROM {table} WHERE name = ?', (name,)
    ).fetchone()
    if row:
        return int(row['id'])
    cur = conn.execute(f'INSERT INTO {table} (name) VALUES (?)', (name,))
    return cur.lastrowid  # type: ignore[return-value]
