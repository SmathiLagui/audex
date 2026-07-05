# Implementation & Design

Reference document for porting the Python scanner to Rust. Covers every design decision, the full data model, algorithms, and the export contract.

---

## Overview

Two operations, both user-triggered:

- **scan** - walk a music folder, detect changes, read tags, persist to SQLite
- **export** - query SQLite, serialise to `export.json`

No background daemon. No file watcher. The user calls scan when they want the library updated.

Windows-only. The change-detection strategy depends on an NTFS-specific kernel timestamp that cannot be spoofed by user-mode tag editors.

---

## Module responsibilities

| Module       | Responsibility                                                  |
| ------------ | --------------------------------------------------------------- |
| `database`   | App paths, SQLite connection setup, schema creation, migrations |
| `repository` | All SQL: find-or-create, upserts, reads, orphan cleanup         |
| `tags`       | Read embedded audio tags -> `RawTags` struct                    |
| `covers`     | Hash cover bytes, write content-addressed file to disk          |
| `windows`    | NTFS ChangeTime via `GetFileInformationByHandleEx`              |
| `scanner`    | Full pipeline: walk -> diff -> tag read -> DB write             |
| `export`     | Query DB, build payload, write `export.json`                    |
| `main`       | CLI entry point (`scan`, `export` commands)                     |

---

## Storage layout

All writes go under `%APPDATA%\ng-player\`:

```text
library.db       SQLite database
export.json      Frontend payload (overwritten on each export)
covers\          Content-addressed cover images: {sha256_hex}.{ext}
logs\            Rotating scan logs (last 9 files kept)
```

The music folder is never written to.

---

## SQLite schema

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS genres (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT    NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS artists (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT    NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS covers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256_hash TEXT    NOT NULL UNIQUE,
    extension   TEXT    NOT NULL                      -- "jpg" or "png"
);

CREATE TABLE IF NOT EXISTS albums (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT    NOT NULL,
    artist_id      INTEGER NOT NULL REFERENCES artists(id),
    year           INTEGER,
    genre_id       INTEGER NOT NULL REFERENCES genres(id),
    cover_id       INTEGER REFERENCES covers(id),
    is_compilation INTEGER NOT NULL DEFAULT 0,
    UNIQUE(title, artist_id)
);

CREATE TABLE IF NOT EXISTS tracks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT,
    artist_id    INTEGER NOT NULL REFERENCES artists(id),
    album_id     INTEGER NOT NULL REFERENCES albums(id),
    track_number INTEGER,                             -- NULL when untagged
    disc_number  INTEGER,                             -- NULL when untagged
    duration_ms  INTEGER NOT NULL,
    path         TEXT    NOT NULL UNIQUE,             -- absolute Windows path
    has_cover    INTEGER NOT NULL DEFAULT 0,          -- 1 if track had embedded art
    bitrate_kbps INTEGER,                             -- NULL for lossless/uncompressed
    audio_format TEXT                                 -- "MP3", "FLAC", "M4A", etc.
);

CREATE TABLE IF NOT EXISTS file_states (
    path           TEXT    NOT NULL PRIMARY KEY,
    size_bytes     INTEGER NOT NULL,
    change_time_ns INTEGER NOT NULL                   -- NTFS ChangeTime, nanoseconds since Unix epoch
);

CREATE INDEX IF NOT EXISTS idx_tracks_album_id  ON tracks(album_id);
CREATE INDEX IF NOT EXISTS idx_tracks_artist_id ON tracks(artist_id);
CREATE INDEX IF NOT EXISTS idx_albums_artist_id ON albums(artist_id);
CREATE INDEX IF NOT EXISTS idx_albums_genre_id  ON albums(genre_id);
```

### Schema design decisions

- `COLLATE NOCASE` on `genres.name` and `artists.name`: the UNIQUE constraint becomes case-insensitive, so "Rock" and "rock" collapse to one row.
- `UNIQUE(title, artist_id)` on albums: "Greatest Hits" by two different artists is two rows.
- No `ON DELETE CASCADE`: orphan cleanup is explicit and intentional (see below).
- `has_cover` on tracks: used to determine whether any track in an album still has embedded art after a partial update, without re-reading unmodified tracks.
- `file_states` is separate from `tracks`: it drives change detection and is not part of the library model the frontend sees.

### Migrations

On every startup, `create_schema` runs `CREATE TABLE IF NOT EXISTS` (idempotent), then checks each known column against `PRAGMA table_info(table)` and runs `ALTER TABLE ... ADD COLUMN` for any missing ones. This makes the schema forward-compatible without a version number.

### Force wipe

`--force` deletes all rows from all tables and resets `sqlite_sequence` so autoincrement IDs restart from 1. This is important for test reproducibility and gives users a clean re-index.

```sql
DELETE FROM tracks;
DELETE FROM file_states;
DELETE FROM albums;
DELETE FROM artists;
DELETE FROM genres;
DELETE FROM covers;
DELETE FROM sqlite_sequence WHERE name IN ('genres','artists','covers','albums','tracks');
```

Cover files on disk are also deleted.

---

## NTFS ChangeTime detection

### The problem

Tag editors (Mp3tag, foobar2000, MusicBrainz Picard) rewrite tags in-place and then restore the original `LastWriteTime` (mtime). File size often stays the same too (padding bytes absorb small tag edits). A stat-only diff misses the vast majority of real-world tag edits.

### The solution

NTFS maintains a fourth timestamp, `ChangeTime`, that the kernel updates whenever the file's MFT record changes (any data write, metadata change, rename). User-mode applications cannot set `ChangeTime` without administrator rights. Tag editors run as normal users - they cannot spoof it.

`ChangeTime` is not exposed by `os.stat()` but is accessible via `GetFileInformationByHandleEx` with `FileBasicInfo`.

### Windows API

```python
# FILE_BASIC_INFO layout (64-byte structure)
CreationTime   int64   # FILETIME (100-ns intervals since 1601-01-01)
LastAccessTime int64
LastWriteTime  int64
ChangeTime     int64   # <- kernel-maintained, cannot be spoofed by user-mode
FileAttributes DWORD
```

Open the file with `FILE_READ_ATTRIBUTES` (0x80) - this is a metadata-only handle that does not read any file data sectors (no disk I/O beyond the MFT entry). Then call `GetFileInformationByHandleEx(handle, FileBasicInfo=0, ...)`.

Convert to nanoseconds since Unix epoch:

```text
unix_ns = (ChangeTime - 116_444_736_000_000_000) * 100
```

The offset `116_444_736_000_000_000` is the number of 100-ns intervals between 1601-01-01 and 1970-01-01.

### Performance

MFT entries are ~1 KB each. For 15 000 files, the entire MFT region is ~15 MB and stays in the OS metadata cache after the first scan. Subsequent refresh runs read ChangeTime from cache - no disk seeks required.

### Rust equivalent

Use `winapi` or `windows` crate:

```rust
use windows::Win32::Storage::FileSystem::{
    CreateFileW, GetFileInformationByHandleEx,
    FileBasicInfo, FILE_BASIC_INFO,
    FILE_READ_ATTRIBUTES, FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_SHARE_DELETE,
    OPEN_EXISTING,
};
```

---

## Tag extraction

### RawTags structure

All formats normalise to this structure before any DB write:

| Field          | Type              | Notes                                     |
| -------------- | ----------------- | ----------------------------------------- |
| `path`         | `String`          | Absolute Windows path                     |
| `title`        | `Option<String>`  |                                           |
| `track_number` | `Option<i32>`     | Parsed from "5/12" -> 5                   |
| `disc_number`  | `Option<i32>`     | Same                                      |
| `duration_ms`  | `i64`             |                                           |
| `track_artist` | `Option<String>`  | TPE1 / ARTIST                             |
| `album_artist` | `Option<String>`  | TPE2 / ALBUMARTIST                        |
| `album_title`  | `Option<String>`  |                                           |
| `year`         | `Option<i32>`     | First 4 chars of date field               |
| `genre`        | `Option<String>`  |                                           |
| `cover_bytes`  | `Option<Vec<u8>>` | Raw image data                            |
| `cover_format` | `Option<String>`  | `"jpg"` or `"png"`                        |
| `bitrate_kbps` | `Option<i32>`     | `None` for WAV/AAC (no lossy bitrate)     |
| `audio_format` | `Option<String>`  | Uppercase extension (`"MP3"`, `"FLAC"` …) |

### Per-format tag keys

| Format   | Library (Rust)   | Track artist | Album artist | Cover                           |
| -------- | ---------------- | ------------ | ------------ | ------------------------------- |
| MP3      | lofty / id3      | TPE1         | TPE2         | APIC frame                      |
| FLAC     | lofty / metaflac | ARTIST       | ALBUMARTIST  | PICTURE block                   |
| M4A      | lofty / mp4ameta | ©ART         | aART         | covr atom                       |
| OGG/Opus | lofty            | ARTIST       | ALBUMARTIST  | METADATA_BLOCK_PICTURE (base64) |
| WAV      | lofty / id3      | TPE1         | TPE2         | APIC frame                      |
| AAC      | lofty            | TPE1         | TPE2         | APIC (if any)                   |

Track/disc numbers often arrive as `"5/12"` (track/total). Parse only the part before `/`.

Bitrate: `bitrate_kbps` is `None` for WAV (uncompressed, no meaningful lossy bitrate) and bare AAC (format does not carry a reliable bitrate field). For others, convert from bps to kbps (`bitrate / 1000`).

**Rust crate:** `lofty` handles all formats with a single API. Use `lofty::read_from_path`.

---

## Cover processing

1. Extract raw bytes + MIME type from the tag.
2. Determine extension: `image/jpeg` -> `"jpg"`, `image/png` -> `"png"`. Other MIME types are ignored.
3. Compute `sha256(bytes)` -> hex string.
4. Write to `covers/{sha256}.{ext}` if not already present (content-addressed, idempotent).
5. Store `(sha256, ext)` in the `covers` table; record the row ID as `cover_id` on the album.

Two albums with identical artwork share one file on disk. Cover files are never modified, only created or deleted.

### Cover assignment

Only one cover is stored per album. During a batch write, the "best" cover seen across all tracks in that album wins - a track with a cover beats a track without one. After the batch, if no track in the batch had a cover, check whether any *other* track in the album (from a prior batch or scan) already has `has_cover = 1` in the DB before clearing the album's `cover_id`.

### Orphan cover cleanup

After deletions, query `covers` for all known hashes and delete any `.jpg`/`.png` file in the covers directory whose stem is not in that set.

---

## Scanner pipeline

### Mode detection

At the start of each `scan_folder` call, count all `file_states` rows. If 0 -> **first index**. Otherwise -> **refresh**.

### First index

```text
walk folder -> collect all audio paths (sorted)
for each batch of 500 paths:
    read tags for each path
    process covers (hash + write to disk)
    open transaction:
        write tracks to DB
        update compilation flags
        record file states (size + ChangeTime)
    commit
```

Batch commits (500 files each) give interrupted-scan recovery for free: on the next run, the already-committed paths appear in `file_states`, so they enter the refresh path as unchanged and are skipped. Unprocessed paths are seen as new.

### Refresh

```text
load all file_states  -> known: Map<path, state>
walk folder           -> on_disk: Map<path, size>

new_paths     = on_disk.keys() - known.keys()
deleted_paths = known.keys() - on_disk.keys()

for path in (on_disk.keys() ∩ known.keys()):
    if on_disk[path].size != known[path].size_bytes:
        size_changed.push(path)
    else:
        size_unchanged.push(path)

for path in size_unchanged:
    ct = get_change_time_ns(path)
    if ct != known[path].change_time_ns:
        change_time_changed.push(path)
    else:
        skipped++

to_read = new_paths + size_changed + change_time_changed

read tags for each path in to_read
process covers
open transaction:
    delete file_states + tracks for deleted_paths
    upsert tracks for to_read
    update compilation flags
    run orphan cleanup if any deletions or updates
    upsert file_states for to_read
commit
```

### Orphan cleanup

Run after any deletions or updates inside the same transaction:

```sql
DELETE FROM albums  WHERE id NOT IN (SELECT DISTINCT album_id  FROM tracks);
DELETE FROM artists WHERE id NOT IN (SELECT DISTINCT artist_id FROM tracks)
                      AND id NOT IN (SELECT DISTINCT artist_id FROM albums);
DELETE FROM genres  WHERE id NOT IN (SELECT DISTINCT genre_id  FROM albums);
DELETE FROM covers  WHERE id NOT IN (
    SELECT DISTINCT cover_id FROM albums WHERE cover_id IS NOT NULL);
```

### Compilation detection

After each write batch, update `is_compilation` for every touched album:

```sql
UPDATE albums
SET is_compilation = (
    SELECT COUNT(DISTINCT artist_id) > 1
    FROM tracks WHERE album_id = albums.id
)
WHERE id IN (?, ?, ...)
```

An album is a compilation when its tracks have more than one distinct `artist_id`.

---

## Export JSON contract

Written to `%APPDATA%\ng-player\export.json`. All keys are camelCase. This is the complete schema as of the current implementation.

```jsonc
{
  "stats": {
    "trackCount":       1234,
    "albumCount":       87,
    "artistCount":      63,
    "genreCount":       12,
    "totalDurationMs":  289340000
  },
  "artists": [
    {
      "id":       1,
      "name":     "Dying Fetus",
      "albumIds": [4, 5]
    }
  ],
  "albums": [
    {
      "id":            4,
      "title":         "Make Them Beg for Death",
      "year":          2023,
      "artistId":      1,
      "genreId":       2,
      "isCompilation": false,
      "trackCount":    11,
      "trackIds":      [30, 31, 32],
      "cover":         "C:\\Users\\...\\covers\\af64eafe1234abcd.jpg"  // null if no art
    }
  ],
  "tracks": [
    {
      "id":           30,
      "title":        "Enlighten Through Agony",
      "artistId":     1,
      "albumId":      4,
      "trackNumber":  1,            // null if untagged
      "discNumber":   1,            // null if untagged
      "durationMs":   247000,
      "bitrateKbps":  null,         // null for lossless/uncompressed
      "audioFormat":  "FLAC",
      "path":         "D:\\Music\\Dying Fetus\\Make Them Beg for Death\\1 -Enlighten Through Agony.flac"
    }
  ],
  "genres": [
    {
      "id":       2,
      "name":     "Brutal Death Metal",
      "albumIds": [4, 5, 9]
    }
  ]
}
```

### Key invariants

- `stats` is the first key. The frontend can display counts without parsing the rest.
- `trackIds` on albums and `albumIds` on artists/genres are pre-computed cross-references. The frontend does not need to scan arrays to build these.
- `cover` is an absolute Windows path. The Angular frontend converts it with `convertFileSrc()` (Tauri asset protocol). The path points to the content-addressed file in the covers directory.
- `trackNumber` and `discNumber` are nullable integers. Untagged tracks sort to the end in the export (SQL `NULLS LAST`).
- `isCompilation` is a boolean. When true, the UI should display per-track `artistId` rather than the album's `artistId` for individual track rows.
- Artists with `albumIds: []` are track artists who are not the credited album artist on any album. They appear in the payload so track detail views can resolve the name by ID.
- IDs are SQLite autoincrement and are not stable across `--force` rescans. Do not persist IDs to disk on the frontend. Use file `path` as the stable identifier (e.g. for playlists).

---

## Recommended Rust crates

| Concern          | Crate                               |
| ---------------- | ----------------------------------- |
| SQLite           | `rusqlite` (with `bundled` feature) |
| Audio tags       | `lofty`                             |
| JSON             | `serde` + `serde_json`              |
| SHA256           | `sha2`                              |
| Windows API      | `windows` crate                     |
| CLI              | `clap`                              |
| Progress display | `indicatif`                         |
| Logging          | `tracing` + `tracing-subscriber`    |
| Error handling   | `anyhow`                            |
