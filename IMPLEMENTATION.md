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

| Module       | Responsibility                                          |
| ------------ | ------------------------------------------------------- |
| `paths`      | App directory paths under `%APPDATA%\ng-player`         |
| `database`   | SQLite connection setup, schema creation, migrations    |
| `repository` | All SQL: find-or-create, upserts, reads, orphan cleanup |
| `tags`       | Read embedded audio tags -> `RawTags` struct            |
| `covers`     | Hash cover bytes, write content-addressed file to disk  |
| `windows`    | NTFS ChangeTime via `GetFileInformationByHandleEx`      |
| `scanner`    | Full pipeline: walk -> diff -> tag read -> DB write     |
| `export`     | Query DB, build payload, write `export.json`            |
| `main`       | CLI entry point (`scan`, `stats`, `export` commands)    |

### SQL boundary rule

All SQL statements live exclusively inside `repository` sub-modules. No raw SQL strings are allowed anywhere else in the codebase. The only exceptions are the `PRAGMA` statements and `CREATE TABLE` / `CREATE INDEX` DDL executed during database initialisation in `database` - those are not query logic and belong there by necessity.

---

## Constants

All shared constant tables are defined once and imported wherever needed. Duplicating them across modules is not allowed.

### Image MIME types

Defined in one place (e.g. `covers` module or a dedicated `constants` module):

| MIME         | Extension |
| ------------ | --------- |
| `image/jpeg` | `jpg`     |
| `image/png`  | `png`     |
| `image/webp` | `webp`    |
| `image/gif`  | `gif`     |
| `image/bmp`  | `bmp`     |

The MIME-to-extension mapping is used in both tag extraction (determining cover format) and cover processing (deciding whether a MIME type is acceptable). Both sites import from the same definition.

```python
COVER_MIME_TO_EXT: dict[str, str] = {
    'jpeg': 'jpg',
    'jpg': 'jpg',
    'png': 'png',
    'webp': 'webp',
    'gif': 'gif',
    'bmp': 'bmp',
}

_COVER_SUFFIXES = frozenset(f'.{ext}' for ext in COVER_MIME_TO_EXT.values())
```

### Audio file extensions / MIME types

The list of recognised audio extensions (used for the filesystem walk filter) is defined once and imported by the scanner. It is not redefined inline at the call site or duplicated in any other module.

```python
AUDIO_EXTENSIONS = frozenset(
    {
        '.mp3',
        '.flac',
        '.m4a',
        '.ogg',
        '.opus',
        '.wav',
        '.aac',
    }
)
```

---

## Storage layout

All writes go under `%APPDATA%\ng-player\`:

```text
library.db       SQLite database
export.json      Frontend payload (overwritten on each export)
covers\          Content-addressed cover images: {content_hash}.{ext}
logs\            Rotating scan logs (last 10 files kept)
```

The music folder is never written to.

---

## Logging

### Log files

Every invocation of the CLI writes a new log file under `%APPDATA%\ng-player\logs\`. Files are named:

```text
scanner_YYYY-MM-DD_HH-mm-ss.log
```

The timestamp comes from loguru's `{time}` token, which expands to the local wall-clock time at startup. Example: `scanner_2024-11-03_14-22-07.log`.

Encoding is UTF-8. Log level is DEBUG (all levels captured).

### Rotation

Before each run opens a new log file, rotation deletes the oldest files so that the total never exceeds 10. The new file is created after pruning, so the cap is always exactly 10 files when more than 10 exist.

### No terminal output

Loguru's default stderr handler is removed at startup (`logger.remove()`). Only the file handler is registered. Nothing from the logger ever reaches the terminal.

All user-facing output (progress bars, result tables, status messages) goes through Rich's `Console`. This keeps the two streams completely separate: structured log records go to the file, human-readable display goes to stdout.

### Log line format

The format is set explicitly on the file sink:

```python
format=(
    '{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | '
    '{name}:{function}:{line} - {message}'
)
```

Which produces:

```text
YYYY-MM-DD HH:mm:ss.SSS | LEVEL     | module:function:line - message
```

Example:

```text
2026-07-06 23:30:49.558 | DEBUG     | audex.repository.tracks:write_tracks:100 - Cover resolved: cover_id=1 for 1 - Hollow Heart.ogg
2026-07-06 23:31:18.019 | INFO      | audex.scanner:_first_index:184 - Tag reading + DB: 13777 ok, 0 error(s) in 29.72s
```

### What is logged

**main** (`audex.main`)

| Level     | When                          | Message                                      |
| --------- | ----------------------------- | -------------------------------------------- |
| INFO      | startup                       | `Run: {full argv}`                           |
| INFO      | user aborts `--force` confirm | `Force re-index aborted by user`             |
| INFO      | KeyboardInterrupt             | `Interrupted by user`                        |
| WARNING   | non-zero SystemExit           | `Exited with code {code}`                    |
| ERROR     | unsupported `--backend`       | `Tag backend {backend!r} is not implemented` |
| EXCEPTION | unhandled top-level exception | `Unhandled exception` (+ full traceback)     |

**tags** (`audex.tags`)

| Level     | When                                   | Message                                              |
| --------- | -------------------------------------- | ---------------------------------------------------- |
| EXCEPTION | tag read raises TagReadError           | `Failed to read tags from {path}` (+ full traceback) |
| WARNING   | cover picture found, MIME not in table | `Unrecognised cover MIME {mime!r} in {filename}`     |

This fires inside `read_tags` before it returns `None`. The caller (`scanner`) then logs a separate WARNING (`Tag read failed (skipped): {path}`). Both entries appear in the log for each failed file.

**scanner** (`audex.scanner`)

| Level   | When                                      | Message                                                                                                      |
| ------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| INFO    | `scan_folder` entry                       | `Tag backend: {backend}`                                                                                     |
| INFO    | `--force` with existing records           | `Force re-index: wiping {N} existing file state(s)`                                                          |
| INFO    | after wipe                                | `Library wiped - starting fresh`                                                                             |
| INFO    | mode detected (first index)               | `Scan mode: first index - {folder} has no existing records`                                                  |
| INFO    | mode detected (refresh)                   | `Scan mode: refresh - {N} known file states for {folder}`                                                    |
| INFO    | after walk (first index)                  | `Walk: {N} audio file(s) found under {folder} in {elapsed}s`                                                 |
| INFO    | after walk (refresh)                      | `Walk: {N} audio file(s) on disk in {elapsed}s`                                                              |
| INFO    | after all batches (first index)           | `Tag reading + DB: {N} ok, {E} error(s) in {elapsed}s`                                                       |
| INFO    | first index done                          | `First index complete: {N} track(s) indexed, {E} error(s) in {total}s`                                       |
| INFO    | after loading DB state (refresh)          | `Refresh: {N} known file state(s) loaded from DB`                                                            |
| INFO    | after size diff (refresh)                 | `Categorization: {new} new, {deleted} deleted, {size_changed} size_changed, {size_unchanged} size_unchanged` |
| INFO    | after ChangeTime checks (refresh)         | `ChangeTime check: {changed} changed, {skipped} skipped in {elapsed}s`                                       |
| INFO    | when there are files to re-read (refresh) | `Reading tags for {N} file(s): {new} new, {size_changed} size_changed, {ct_changed} ct_changed`              |
| INFO    | when all files are up to date (refresh)   | `No files need tag re-reading - all up to date`                                                              |
| INFO    | tag read done (refresh)                   | `Tag reading: {N} ok, {E} error(s) in {elapsed}s`                                                            |
| INFO    | when covers extracted (refresh)           | `Cover extraction: {N} file(s) in {elapsed}s`                                                                |
| INFO    | after DB transaction                      | `DB operations in {elapsed}s`                                                                                |
| INFO    | after orphan cleanup, files deleted       | `{N} orphan cover file(s) deleted from disk`                                                                 |
| INFO    | refresh done                              | `Refresh complete: {N} new, {N} updated, {N} deleted, {N} skipped, {E} error(s) in {total}s`                 |
| DEBUG   | per file, before tag read                 | `Reading tags: {absolute path}`                                                                              |
| DEBUG   | per new file detected during diff         | `New file detected: {path}`                                                                                  |
| DEBUG   | per deleted file detected during diff     | `Deleted file detected: {path}`                                                                              |
| DEBUG   | per file, size changed during diff        | `Size changed: {filename} ({old} -> {new} bytes)`                                                            |
| DEBUG   | start of ChangeTime loop                  | `ChangeTime check: {N} size-unchanged file(s) to inspect`                                                    |
| DEBUG   | per file, ChangeTime changed              | `ChangeTime changed: {filename} ({old_ns} -> {new_ns})`                                                      |
| DEBUG   | per track with embedded art               | `Cover: {hash[:12]}.{ext} ({bytes} bytes) <- {filename}`                                                     |
| DEBUG   | after cover processing batch              | `_process_covers: {N}/{total} tracks had embedded art`                                                       |
| DEBUG   | after orphan cleanup, nothing to delete   | `No orphan cover files to delete`                                                                            |
| WARNING | tag read exception                        | `Tag read failed (skipped): {path}`                                                                          |
| WARNING | cover process failure (bad MIME etc.)     | `Could not process cover for {path}`                                                                         |
| WARNING | ChangeTime read failure (file re-read)    | `Could not read ChangeTime for {path} - will re-read tags`                                                   |
| WARNING | file state record failure                 | `Could not record file state for {path}`                                                                     |

**repository.tracks** (`audex.repository.tracks`)

| Level     | When                                          | Message                                                                               |
| --------- | --------------------------------------------- | ------------------------------------------------------------------------------------- |
| DEBUG     | per track with a cover                        | `Cover resolved: cover_id={id} for {filename}`                                        |
| DEBUG     | per track written                             | `Track upserted: "{title}" / "{album}" [{filename}]`                                  |
| DEBUG     | per album after batch, cover updated          | `Album cover set: album_id={id} cover_id={id}`                                        |
| DEBUG     | per album after batch, no art anywhere        | `Album cover cleared: album_id={id} (no track has embedded art)`                      |
| DEBUG     | per album after batch, art in existing tracks | `Album cover kept: album_id={id} ({N} track(s) still have embedded art)`              |
| DEBUG     | end of each batch                             | `write_tracks: {N} written, {N} album(s) cover-updated`                               |
| EXCEPTION | DB error on a single track                    | `Failed to write track to DB: {path}` (+ traceback; track is skipped, scan continues) |

**export** (`audex.export`)

| Level | When                          | Message                                                                                     |
| ----- | ----------------------------- | ------------------------------------------------------------------------------------------- |
| INFO  | export starts                 | `Export started`                                                                            |
| DEBUG | after each entity type loaded | `{N} genre(s) loaded`, `{N} artist(s) loaded`, `{N} album(s) loaded`, `{N} track(s) loaded` |
| DEBUG | before JSON write             | `Serialising payload to {path}`                                                             |
| INFO  | export done                   | `Export complete: {N} tracks, {N} albums, {N} artists -> {path} in {elapsed}s`              |

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
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT    NOT NULL UNIQUE,
    extension    TEXT    NOT NULL                      -- "jpg", "png", "webp", "gif", "bmp"
);

CREATE TABLE IF NOT EXISTS albums (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT    NOT NULL COLLATE NOCASE,
    artist_id      INTEGER NOT NULL REFERENCES artists(id) ON DELETE RESTRICT,
    year           INTEGER,
    genre_id       INTEGER NOT NULL REFERENCES genres(id) ON DELETE RESTRICT,
    cover_id       INTEGER REFERENCES covers(id),
    is_compilation INTEGER NOT NULL DEFAULT 0,
    UNIQUE(title, artist_id)
);

CREATE TABLE IF NOT EXISTS tracks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT,
    artist_id    INTEGER NOT NULL REFERENCES artists(id) ON DELETE RESTRICT,
    album_id     INTEGER NOT NULL REFERENCES albums(id) ON DELETE RESTRICT,
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

- `COLLATE NOCASE` on `genres.name`, `artists.name`, and `albums.title`: the UNIQUE constraints become case-insensitive, so "Rock"/"rock" and "Greatest Hits"/"greatest hits" (same artist) each collapse to one row.
- `UNIQUE(title, artist_id)` on albums: "Greatest Hits" by two different artists is two rows.
- No `ON DELETE CASCADE`: orphan cleanup is explicit and intentional (see below).
- `ON DELETE RESTRICT` on all non-cover foreign keys: albums cannot be deleted while tracks reference them; artists and genres cannot be deleted while albums or tracks reference them. The orphan cleanup always deletes in the correct order (tracks first, then albums, then artists/genres) so these constraints are never violated during normal operation - they exist to catch bugs, not to block valid operations.
- `covers.id` has no `ON DELETE RESTRICT`: cover rows are cleaned up last after the referencing albums are already updated (cover_id set to NULL or changed), so there is nothing to restrict.
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

| Field          | Type              | Notes                                        |
| -------------- | ----------------- | -------------------------------------------- |
| `path`         | `String`          | Absolute Windows path                        |
| `title`        | `Option<String>`  |                                              |
| `track_number` | `Option<i32>`     | Parsed from "5/12" -> 5                      |
| `disc_number`  | `Option<i32>`     | Same                                         |
| `duration_ms`  | `i64`             |                                              |
| `track_artist` | `Option<String>`  | TPE1 / ARTIST                                |
| `album_artist` | `Option<String>`  | TPE2 / ALBUMARTIST                           |
| `album_title`  | `Option<String>`  |                                              |
| `year`         | `Option<i32>`     | First 4 chars of date field                  |
| `genre`        | `Option<String>`  |                                              |
| `cover_bytes`  | `Option<Vec<u8>>` | Raw image data                               |
| `cover_format` | `Option<String>`  | `"jpg"`, `"png"`, `"webp"`, `"gif"`, `"bmp"` |
| `bitrate_kbps` | `Option<i32>`     | `None` for WAV/AAC (no lossy bitrate)        |
| `audio_format` | `Option<String>`  | Uppercase extension (`"MP3"`, `"FLAC"` …)    |

### Missing field behaviour

`RawTags` carries raw optional values. The resolution to DB entities happens in `write_tracks` and applies these rules in order:

| Field          | Missing behaviour                                                        |
| -------------- | ------------------------------------------------------------------------ |
| `genre`        | stored as `'Unknown'`                                                    |
| `track_artist` | stored as `'Unknown Artist'`                                             |
| `album_artist` | falls back to `track_artist`; if that is also absent, `'Unknown Artist'` |
| `album_title`  | stored as `'Unknown Album'`                                              |
| `title`        | stored as `NULL` in DB (nullable column)                                 |
| `track_number` | stored as `NULL` (sorts last in export)                                  |
| `disc_number`  | stored as `NULL` (sorts last in export)                                  |
| `year`         | stored as `NULL`                                                         |
| `bitrate_kbps` | stored as `NULL`                                                         |
| `audio_format` | stored as `NULL` (in practice always set by the backends)                |
| `cover_bytes`  | no cover stored; `has_cover = 0` on the track                            |

The `album_artist -> track_artist` fallback is the most important one: on a file where only `ARTIST` / `TPE1` is tagged (no `ALBUMARTIST` / `TPE2`), the track artist is also used as the album artist. This is the correct behaviour for single-artist releases tagged by tools that omit the album artist field.

### Per-format tag keys

| Format   | Library (Python)           | Track artist | Album artist | Cover                           |
| -------- | -------------------------- | ------------ | ------------ | ------------------------------- |
| MP3      | pytaglib / mutagen.id3     | TPE1         | TPE2         | APIC frame                      |
| FLAC     | pytaglib / mutagen.flac    | ARTIST       | ALBUMARTIST  | PICTURE block                   |
| M4A      | pytaglib / mutagen.mp4     | ©ART         | aART         | covr atom                       |
| OGG/Opus | pytaglib / mutagen.oggopus | ARTIST       | ALBUMARTIST  | METADATA_BLOCK_PICTURE (base64) |
| WAV      | pytaglib / mutagen.wave    | TPE1         | TPE2         | APIC frame                      |
| AAC      | pytaglib / mutagen.aac     | TPE1         | TPE2         | APIC (if any)                   |

Track/disc numbers often arrive as `"5/12"` (track/total). Parse only the part before `/`.

Bitrate: `bitrate_kbps` is `None` for WAV (uncompressed, no meaningful lossy bitrate) and bare AAC (format does not carry a reliable bitrate field). For others, convert from bps to kbps (`bitrate / 1000`).

**Python:** `pytaglib` (default, wraps TagLib C++) handles all formats via a unified dict API. `mutagen` is an opt-in pure-Python alternative (`--backend mutagen`).

**Rust crate:** `lofty` handles all formats with a single API. Use `lofty::read_from_path`.

---

## Cover processing

1. Extract raw bytes + MIME type from the tag (rules below).
2. Determine extension from MIME type using the shared MIME-to-extension table defined in the Constants section. If the declared MIME does not map to a known extension, sniff the actual format from the image data's magic bytes (via the `filetype` library) before giving up, then map *that* detected MIME through the same table. Some taggers write a malformed or bogus MIME string (observed in the wild: `image/2`, `image/` with no subtype, empty string) while the embedded image data itself is intact - trusting the declared MIME alone drops real covers unnecessarily. A format `filetype` detects but that isn't in the MIME-to-extension table (e.g. tiff, heic) is still rejected, same as an unrecognised declared MIME. Only if neither the declared MIME nor the sniffed format resolve to a known extension is the picture ignored and the track treated as having no cover (for that candidate - see the fallback-through-invalid-candidates rule below).
3. Compute `xxh3_128(bytes)` -> hex string (`content_hash`).
4. Write to `covers/{content_hash}.{ext}` if not already present (content-addressed, idempotent).
5. Store `(content_hash, ext)` in the `covers` table; record the row ID as `cover_id` on the album.

Two albums with identical artwork share one file on disk. Cover files are never modified, only created or deleted.

### Per-format extraction rules

These rules are what the Python implementation (pytaglib backend) does. The Rust port must replicate them exactly - deviations are the reason for cover count mismatches.

**pytaglib fallback-through-invalid-candidates rule (all formats):** pytaglib reads every container through TagLib's unified `pictures` list, so in practice all formats below share one algorithm: build an ordered candidate list per the per-format rule (FLAC puts any `Front Cover`-typed picture(s) first, then the rest in their original order; other formats keep natural order), then walk the candidates in order and take the **first one whose MIME maps to `jpg`/`png` AND whose data is non-empty**. A candidate that fails validation (e.g. a malformed MIME like `image/` with no subtype, seen in the wild from some legacy taggers) is skipped rather than treated as "no cover" - the next candidate in the list is tried instead. Only when every candidate fails validation, or there are no pictures at all, does the track have no cover. This matters for real files with multiple embedded pictures where the first one is corrupt/malformed but a later one is valid.

#### MP3 / WAV / AAC (ID3 container)

ID3 stores covers as `APIC` frames. The frame key is `APIC:<description>`, where description is a free-text string, most commonly empty. The lookup must try all three variants in order:

1. `APIC:` (standard empty-description key, most common)
2. `APIC` (key without colon, produced by some editors)
3. Scan all frame keys that start with `APIC` and take the first match (catches `APIC:Cover`, `APIC:Front`, etc.)

No picture-type preference for ID3 - there is no reliable type field to filter on. Candidate order is frame-list order; validation and fallback follow the pytaglib rule above.

#### FLAC (PICTURE blocks)

The two backends deliberately differ here, and both behaviours must be preserved - pytaglib is the reference for cover counts (it's the default backend).

**pytaglib** (via TagLib's unified `pictures` list, `picture_type` as a string):

1. Candidate order: pictures where `picture_type == 'Front Cover'` first, then the rest in original order.
2. Validate MIME (must map to `jpg`/`png`) and data (non-empty) per the fallback rule above; the first candidate to pass wins.

**mutagen** (via FLAC's raw Picture blocks, `type` as a numeric field - 3 = Front Cover, 0 = Other):

Iterate the picture list and take the first entry whose type is 3 or 0, whose MIME maps to `jpg`/`png`, and whose data is non-empty. Skip entries with any other type entirely. If no entry passes the filter, the track has no cover - mutagen does **not** fall back to an arbitrary first picture the way pytaglib does.

A Rust port that only implements one backend should replicate pytaglib's fallback behaviour, since that is the default and the reference for cover counts. Filtering to only types 3 and 0 with no fallback (the mutagen rule) would cause misses on files that have art tagged with a different type.

#### M4A (MP4 `covr` atom)

Candidate order is `covr[]` atom order. Accept only `FORMAT_JPEG` or `FORMAT_PNG` image formats; validation and fallback-to-next-candidate follow the pytaglib rule above. M4A is limited to these two types by the MP4 spec - the extended MIME table above does not apply here.

#### OGG / Opus (Vorbis comment `METADATA_BLOCK_PICTURE`)

The picture(s) are stored as a Vorbis comment tag `METADATA_BLOCK_PICTURE`, each value a base64-encoded FLAC Picture structure. Candidate order is tag-value order; base64-decode each, parse as a FLAC Picture, then validate MIME and data per the pytaglib rule above. No picture-type filtering.

### Cover assignment

Only one cover is stored per album. During a batch write, the "best" cover seen across all tracks in that album wins - a track with a cover beats a track without one. After the batch, if no track in the batch had a cover, check whether any *other* track in the album (from a prior batch or scan) already has `has_cover = 1` in the DB before clearing the album's `cover_id`.

### Orphan cover cleanup

After deletions, query `covers` for all known hashes and delete any cover file (`.jpg`, `.png`, `.webp`, `.gif`, `.bmp`) in the covers directory whose stem is not in that set.

---

## Scanner pipeline

### Terminal progress display

All progress is rendered through Rich's `Progress` widget (Python). The walk phase uses an indeterminate spinner because the total file count is not known upfront - it does not block on knowing the count before displaying feedback.

| Phase                       | Display style                                        |
| --------------------------- | ---------------------------------------------------- |
| Walking files (first index) | Indeterminate spinner (`total=None`)                 |
| Walking files (refresh)     | Indeterminate spinner (`total=None`)                 |
| ChangeTime check            | Determinate bar + ETA (total = size_unchanged count) |
| Reading tags                | Determinate bar + ETA (total = files to read)        |

The walk spinner is enough to show the process is not frozen. The file count appears in the log once the walk completes.

### Mode detection

At the start of each `scan_folder` call, count all `file_states` rows. If 0 -> **first index**. Otherwise -> **refresh**.

### First index

```text
walk folder -> collect all audio paths (sorted)
for each batch of 500 paths:
    read tags for each path          <- all disk I/O first
    process covers (hash + write to disk)
    open transaction:
        for each track:
            find-or-create genre, artists, cover
            find-or-create album
                -> if the (title, artist_id) row already exists, its
                   year and genre_id are overwritten from the freshly
                   read tags, so a tag edit to an already-indexed album
                   is reflected without a separate update path
            upsert track
            accumulate album_best_cover
        for each touched album:
            update album cover from album_best_cover
        update compilation flags
        upsert file states (size + ChangeTime)
    commit
```

Tag reading and DB writing are strictly separated within each batch. All disk I/O (tag reads, cover file writes) finishes before the transaction opens. This avoids interleaving HDD seeks between audio files and the SQLite WAL. Album covers are updated after all tracks in the batch because `album_best_cover` must be accumulated across the full batch before any album row is touched - you cannot know the best cover for an album until every track in that batch has been seen.

This processing order is reflected in the log output and must be preserved in the Rust port.

Batch commits (500 files each) give interrupted-scan recovery for free: on the next run, the already-committed paths appear in `file_states`, so they enter the refresh path as unchanged and are skipped. Unprocessed paths are seen as new.

### Refresh

```text
load all file_states  -> known: Map<path, state>
walk folder           -> on_disk: Map<path, size>

new_paths     = on_disk.keys() - known.keys()
deleted_paths = known.keys() - on_disk.keys()

for path in on_disk.keys().intersection(known.keys()):
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
        -> record each deleted track's former album_id (albums_missing_a_track)
    upsert tracks for to_read
    update compilation flags for albums touched by upserts
    update compilation flags for albums_missing_a_track
        -> a deletion can turn a compilation album back into a single-artist
           album; only recomputing flags for upserted albums would leave
           this stale
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

"Touched" means both albums that received an upserted track *and* albums that
lost a track during refresh's deletion step. A deletion can turn a
compilation back into a single-artist album, so the id list passed to this
`UPDATE` must include `albums_missing_a_track` from the refresh pipeline
above, not just the albums touched by upserts.

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

### Ordering

Every array in the payload has a defined order. Implementations must reproduce it exactly.

| Array / field       | Order                                                               |
| ------------------- | ------------------------------------------------------------------- |
| `artists[]`         | `name` ascending (alphabetical, SQLite default collation)           |
| `genres[]`          | `name` ascending (alphabetical, SQLite default collation)           |
| `albums[]`          | `id` ascending (insertion / scan order)                             |
| `tracks[]`          | `album_id` asc, `disc_number NULLS LAST`, `track_number NULLS LAST` |
| album `trackIds[]`  | `disc_number NULLS LAST`, `track_number NULLS LAST`                 |
| artist `albumIds[]` | album insertion order (albums are iterated by `id`)                 |
| genre `albumIds[]`  | album insertion order (albums are iterated by `id`)                 |

Notes:

- `NULLS LAST` means untagged tracks (no disc or track number) sort after numbered ones within the same album.
- `artists[]` and `genres[]` sort by name, so the arrays change order across `--force` rescans as IDs are reassigned.
- `albums[]` sorted by `id` means albums appear in the order they were first written to the DB (i.e. filesystem walk order from the first index). This is stable within a run, not lexicographic.
- `albumIds` on artists and genres follow album insertion order for the same reason - they are built by iterating the `albums[]` result (already ordered by `id`) and appending.

### Key invariants

- `stats` is the first key. The frontend can display counts without parsing the rest.
- `trackIds` on albums and `albumIds` on artists/genres are pre-computed cross-references. The frontend does not need to scan arrays to build these.
- `cover` is an absolute Windows path. The Angular frontend converts it with `convertFileSrc()` (Tauri asset protocol). The path points to the content-addressed file in the covers directory.
- `trackNumber` and `discNumber` are nullable integers. Untagged tracks sort to the end within their album (see ordering table above).
- `isCompilation` is a boolean. When true, the UI should display per-track `artistId` rather than the album's `artistId` for individual track rows.
- Artists with `albumIds: []` are track artists who are not the credited album artist on any album. They appear in the payload so track detail views can resolve the name by ID.
- IDs are SQLite autoincrement and are not stable across `--force` rescans. Do not persist IDs to disk on the frontend. Use file `path` as the stable identifier (e.g. for playlists).

---

## Stats command

Read-only query. Exits with an error if the database does not exist.

Displays a Rich table with:

| Row      | Source                                                           |
| -------- | ---------------------------------------------------------------- |
| Tracks   | `COUNT(*)` from `tracks`                                         |
| Albums   | `COUNT(*)` from `albums`                                         |
| Artists  | `COUNT(*)` from `artists`                                        |
| Genres   | `COUNT(*)` from `genres`                                         |
| Duration | `SUM(duration_ms)` from `tracks`, formatted with cascading units |

Also prints the database path and its size in MB above the table.

Duration formatting always shows hours/minutes/seconds, and prepends coarser
units once the duration crosses their threshold: `Xy XXmo XXd XXh XXm XXs`
once it exceeds 12 months, else `Xmo XXd XXh XXm XXs` once it exceeds 30
days, else `Xd XXh XXm XXs` once it exceeds 24 hours, else `Xh XXm XXs`, else
`Xm XXs` below 1 hour. Months and years use calendar approximations (30-day
months, 12-month years), not calendar-accurate ones.

No log entries specific to this command. The initial `Run: {argv}` INFO line from `main` still fires.
