# audex

Python CLI audio library scanner - POC component of the ng-player Tauri desktop app.

Indexes a music folder into SQLite and exports the database as a single JSON file consumed by the Angular frontend. Scan and export are user-triggered; there is no background daemon or file watcher.

---

## Requirements

- Windows 10+ (scan uses NTFS ChangeTime - see [IMPLEMENTATION.md](IMPLEMENTATION.md))
- [mise](https://mise.jdx.dev/) - manages the full toolchain (Python, uv)

---

## Setup

```sh
mise install   # installs Python and uv at the versions declared in mise.toml
mise run i     # alias for: uv sync --frozen
```

---

## Commands

### `scan`

Index one or more music folders.

```sh
mise app:scan <FOLDER>
# or directly:
audex scan <FOLDER>
```

Options:

| Flag                | Default    | Description                                         |
| ------------------- | ---------- | --------------------------------------------------- |
| `--force` / `-f`    | off        | Wipe the existing library and re-index from scratch |
| `--backend` / `-b`  | `pytaglib` | Tag reading backend: `pytaglib` or `mutagen`        |

On first run, every file is read and indexed (slow - proportional to library size and disk speed).
On subsequent runs, only changed files are re-processed using NTFS ChangeTime detection.

### `export`

Export the library to JSON for the frontend.

```sh
mise  app:export
# or directly:
audex export
```

Writes to `%APPDATA%\ng-player\export.json`.
Covers are resolved from `%APPDATA%\ng-player\covers\`.
Requires a prior `scan` run.

---

## Data locations

| Path                                              | Contents                                          |
| ------------------------------------------------- | ------------------------------------------------- |
| `%APPDATA%\ng-player\library.db`          | SQLite database                                   |
| `%APPDATA%\ng-player\export.json`         | Frontend payload                                  |
| `%APPDATA%\ng-player\covers\`             | Content-addressed cover images (`{sha256}.{ext}`) |
| `%APPDATA%\ng-player\logs\`               | Rotating scan logs (last 9 kept)                  |

---

## Supported formats

`.mp3` `.flac` `.m4a` `.ogg` `.opus` `.wav` `.aac`

---

## Development

Available mise tasks:

```sh
mise test     # pytest with coverage
mise lint     # ruff check + mypy
mise format   # ruff import sort + format
```

Tests create real files on disk (via `tmp_path`) and mock only the two Windows-specific / audio-specific callsites: `get_change_time_ns` and `tags.read_tags`.

---

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md) - source available, non-commercial use only.
