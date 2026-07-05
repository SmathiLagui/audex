# Audio Library Scanner - Implementation Brief

## Project context

A Windows-only Python library embedded in a Tauri desktop app (Angular frontend, Rust backend). The Python side has two responsibilities: index a music folder into SQLite, and export the database as a single JSON blob consumed by the frontend. There is no background daemon, no file watcher. Both operations are explicitly user-triggered via a CLI (dev convenience only — the Rust port will embed the logic directly).

> For the ease of developpement, the scan and export to json process are different. scan must ask for a folder and export always goes to `%APPDATA%\ng-player\export.json`
---
> For the "UI" part of the console, `rich` is used

**Current stack:** Python 3.14+, pydantic v2, SQLite, `loguru` for logging (and log rotation), `uv` for package management.

**Hard constraints:**

- Windows only - platform-specific APIs are acceptable and encouraged.
- All models must use pydantic `BaseModel`. No `dataclasses`.

  ```python
  from pydantic import BaseModel, ConfigDict
  from pydantic.alias_generators import to_camel
  
  class SchemaBaseModel(BaseModel):
    model_config = ConfigDict(
      # Allow model_validate to transfrom from db models
      from_attributes=True,
      # get camelCased data from api
      alias_generator=to_camel,
      validate_by_name=True,
    )
  ```

- No JSON blobs in SQL columns. Every field gets a typed column.
- The music folder is read-only. All writes go to SQLite and a covers directory under `%APPDATA%\ng-player` (json goes to `export.json`, covers in `covers` folder and logs in `logs` folder).

---

## What must be extracted from audio files

Each audio file (MP3, FLAC, M4A, OGG, Opus, WAV, AAC) carries embedded tags. The following must be extracted per file:

- Title, track number, disc number, duration
- Track artist (the performer of this specific track)
- Album artist (the credited artist for the whole album - often different from track artist on compilations or features)
- Album title, year, genre
- Embedded cover image (raw bytes + format)
- Bitrate (kbps) and audio format (uppercase extension string)

Tag reading is the expensive operation. Everything else in the pipeline is cheap by comparison.

---

## Database schema

The schema supports the following entities:

- **Artist** - deduplicated by name. An artist can be an album artist, a track interpreter, or both.
- **Album** - belongs to one album artist. Has title, year, genre, cover, and an `is_compilation` flag.
- **Track** - belongs to one album. Has its own interpreter (may differ from album artist). Carries the absolute file path, bitrate, and audio format.
- **Genre** - deduplicated by name. One genre per album.
- **Cover** - deduplicated by content hash. Stored on disk as `{sha256}.{ext}` under `%APPDATA%\ng-player\covers\`. The DB stores the hash and extension; the file is the source of truth.
- **File state** - stores `path`, `size_bytes`, and `change_time_ns` (NTFS ChangeTime). Used to detect changes without re-reading tags on every run.

---

## What the frontend receives (`export.json`)

The frontend loads this once into memory and never queries SQLite again. All entities are cross-referenced by integer ID.

```json
{
  "stats": {
    "trackCount": 1234,
    "albumCount": 87,
    "artistCount": 63,
    "genreCount": 12,
    "totalDurationMs": 289340000
  },
  "artists": [
    {
      "id": 1,
      "name": "Dying Fetus",
      "albumIds": [4, 5]
    }
  ],
  "albums": [
    {
      "id": 4,
      "title": "Make Them Beg for Death",
      "year": 2023,
      "artistId": 1,
      "genreId": 2,
      "isCompilation": false,
      "trackCount": 11,
      "trackIds": [30, 31, 32],
      "cover": "C:\\Users\\user\\AppData\\Roaming\\ng-player\\covers\\af64eafe1234abcd.jpg"
    }
  ],
  "tracks": [
    {
      "id": 30,
      "title": "Enlighten Through Agony",
      "artistId": 1,
      "albumId": 4,
      "trackNumber": 1,
      "discNumber": 1,
      "durationMs": 247000,
      "bitrateKbps": null,
      "audioFormat": "FLAC",
      "path": "D:\\Music\\Dying Fetus\\Make Them Beg for Death\\01 Enlighten Through Agony.flac"
    }
  ],
  "genres": [
    {
      "id": 2,
      "name": "Brutal Death Metal",
      "albumIds": [4, 5, 9]
    }
  ]
}
```

**Artist note:** a track interpreter who is not the album artist on any album in the library still appears in `artists` with `album_ids: []`. The UI uses `album_ids.length > 0` to decide what to show in the album artist list. Track detail and search look up any artist by ID regardless.

**Cover note:** `cover` is the filename of a content-addressed file on disk (`%APPDATA%\ng-player\covers\{sha256}.{ext}`), or `null` if the album has no embedded art. Two albums sharing identical artwork share one file on disk.

---

## The two operations

**First index** - user opens the app for the first time, points it at a folder. The library is empty. Every file must be read. The user accepts that this is slow. Correctness and completeness are the only requirements.

**Refresh** - user has made changes to the folder (added albums, deleted tracks, edited tags, changed embeded artwork) and triggers an update. The library already exists. Reading every file again is not acceptable. Only changed files should be re-processed.

---

## The core problem

**Stat fields are unreliable.** The natural approach for refresh is to check `mtime` and file size: if unchanged, skip the file. This does not work in practice. Most tag editors on Windows (Mp3tag, MusicBrainz Picard, foobar2000, etc.) rewrite tags in-place without updating the filesystem timestamp, and the size often stays identical too. A stat-only diff will silently miss the majority of real-world tag edits.

**Reading 15 000 files on a spinning disk is slow.** The per-file read cost is small but the seek cost is not. On a 7200 RPM HDD, random access to 15 000 different file locations can take several minutes. Sequential access to the same data takes under 30 seconds. Concurrency makes it worse on HDD, not better - multiple threads pulling the disk head in different directions destroys throughput.

---

## What the implementation must figure out

1. **What to store in `file_states`** and how to use it to detect changes on refresh - both for the common case (stat changes) and the hard case (tag edit with preserved mtime+size).

2. **How to structure the refresh pipeline** so it is correct (detects all changes) and fast on HDD (avoids random-access I/O patterns).

3. **What libraries to use** - the current stack is a starting point, not a constraint. If a better approach requires different or additional libraries, propose them with justification.

4. **Whether first index and refresh share a pipeline** or are better implemented as two distinct paths with different trade-offs.

5. **How to keep the export fast** as the library grows - the full graph serialisation is currently done on every export call.
