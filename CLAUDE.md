# Audio Library Scanner - Implementation Brief

> **Always read [IMPLEMENTATION.md](IMPLEMENTATION.md) before any analysis, review, or implementation task on this codebase.** It is the authoritative, detailed design spec (exact algorithms, SQL, ordering guarantees, per-format cover extraction rules, logging contract) that this brief only summarises. Code must conform to it; treat mismatches between code and IMPLEMENTATION.md as bugs.

## Project context

A Windows-only Python library embedded in a Tauri desktop app (Angular frontend, Rust backend). The Python side has two responsibilities: index a music folder into SQLite, and export the database as a single JSON blob consumed by the frontend. There is no background daemon, no file watcher. Both operations are explicitly user-triggered via a CLI (dev convenience only - the Rust port will embed the logic directly).

> For the ease of developpement, the scan and export to json process are different. scan must ask for a folder and export always goes to `%APPDATA%\ng-player\export.json`
---
> For the "UI" part of the console, `rich` is used

**Current stack:** Python 3.14+, pydantic v2, SQLite, `loguru` for logging (and log rotation), `uv` for package management, `mise` as task runner.

**Tooling:** All dev tasks (lint, format, test) are run via `mise run <task>`. Never call `uv run ruff`, `uv run mypy`, `uv run pytest`, etc. directly. Discover available tasks with `mise tasks`.

**Git commits:** Never add Claude (or any AI) as a commit co-author or author. Commits are authored by the user only - no `Co-Authored-By: Claude ...` trailer.

**Typography:** Never use characters that are not typeable on a standard keyboard (em dash, Unicode arrows, curly quotes, etc.). Use plain ASCII equivalents: hyphen `-` for dashes, `->` for arrows. Applies to all written output: Markdown, comments, docstrings, and assistant responses.

**Set operators:** Use set methods (`.union()`, `.intersection()`, `.difference()`) instead of the `|`/`&`/`-` operators. More descriptive and readable at a glance.

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

**Naming:** Use `_` for a variable whose value is intentionally unused, instead of giving it a real name.

**Control flow:** Avoid nesting as much as possible, in both functions and `for`/`while` loops. Prefer guard clauses / early returns over wrapping a function body in nested `if`s; prefer flattening loops with `continue` over nested conditionals inside them.

**File size:** When a file becomes too large to work with comfortably, split it into a sub-package (a directory with an `__init__.py`) rather than letting it keep growing - even if only a single name ends up exposed from `__init__.py`. Private helpers used only within that sub-package don't need to be exposed - keep them internal to their module.

**Testing conventions:**

- Avoid `unittest.mock` as much as possible - prefer installed pytest plugins instead (e.g. `pytest-mock`'s `mocker` fixture).
- Never use `mocker.patch` - use `mocker.patch.object` instead.
- Never prefix autouse fixtures with `_`. Nothing from tests is imported outside of tests (or its own file), so the underscore prefix serves no purpose.

**Formatting:** Never format any files (whitespace, alignment, style) - the user handles that themselves.

## What to extract, the schema, the export contract, and why

All of that - per-file tag fields, database schema, the `export.json` shape, first-index vs refresh, the ChangeTime-based diff strategy, batching, backends - is specified precisely in [IMPLEMENTATION.md](IMPLEMENTATION.md). This brief does not restate it; see the note at the top of this file.
