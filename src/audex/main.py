import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger
from rich.console import Console
from rich.progress import Progress, TimeElapsedColumn
from rich.table import Table

from . import export as export_mod
from . import repository as repo
from . import scanner as scanner_mod
from .database import create_schema, open_connection
from .paths import get_covers_dir, get_db_path, get_export_path, get_logs_dir
from .tags import TagBackend

MAX_LOG_FILES = 10

app = typer.Typer(add_completion=False)
console = Console()


def _run_export(conn: sqlite3.Connection, out_path: Path) -> None:
    covers_dir = get_covers_dir()
    t0 = time.perf_counter()
    with console.status('Exporting library...'):
        export_mod.export_library(conn, covers_dir, out_path)
    elapsed = time.perf_counter() - t0
    console.print(f'Exported to [bold]{out_path}[/bold] in {elapsed:.2f}s')


def _format_duration(ms: int) -> str:
    total_s = ms // 1000
    m, s = divmod(total_s, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    mo, d = divmod(d, 30)
    y, mo = divmod(mo, 12)

    if y:
        return f'{y}y {mo}mo {d}d {h}h {m:02d}m {s:02d}s'
    if mo:
        return f'{mo}mo {d}d {h}h {m:02d}m {s:02d}s'
    if d:
        return f'{d}d {h}h {m:02d}m {s:02d}s'
    if h:
        return f'{h}h {m:02d}m {s:02d}s'
    return f'{m}m {s:02d}s'


def _prune_old_logs(logs_dir: Path) -> None:
    files = sorted(
        logs_dir.glob('scanner_*.log'),
        key=lambda f: f.stat().st_mtime,
    )
    # Prune to MAX_LOG_FILES-1; the new file brings the total to MAX_LOG_FILES
    to_delete = (
        files[: -(MAX_LOG_FILES - 1)] if len(files) >= MAX_LOG_FILES else []
    )
    for old in to_delete:
        old.unlink(missing_ok=True)


def _setup_logging() -> None:
    logger.remove()
    logs_dir = get_logs_dir()
    _prune_old_logs(logs_dir)
    logger.add(
        logs_dir / 'scanner_{time}.log',
        level=logging.DEBUG,
        encoding='utf-8',
    )
    logger.info('Run: {}', ' '.join(sys.argv))


@app.command()
def scan(
    folder: Annotated[
        Path,
        typer.Argument(
            help='Music folder to scan.',
            file_okay=False,
            resolve_path=True,
        ),
    ],
    force: Annotated[
        bool,
        typer.Option(
            '--force',
            '-f',
            help='Wipe existing library and re-index.',
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            '--yes',
            '-y',
            help='Skip confirmation prompt for --force.',
        ),
    ] = False,
    backend: Annotated[
        TagBackend,
        typer.Option(
            '--backend',
            '-b',
            help='Tag reading backend.',
        ),
    ] = TagBackend.PyTagLib,
    export_after: Annotated[
        bool,
        typer.Option(
            '--export',
            '-e',
            help='Export library to JSON after scan.',
        ),
    ] = False,
) -> None:
    """Index a music folder into the local library database."""
    if sys.platform != 'win32':
        console.print(
            '[red]scan requires Windows (NTFS ChangeTime detection)[/red]'
        )
        raise typer.Exit(1)

    db_path = get_db_path()
    covers_dir = get_covers_dir()
    console.print(f'Database: {db_path}')
    console.print(f'Scanning [bold]{folder}[/bold]')

    with open_connection(db_path) as conn:
        create_schema(conn)

        if force and not yes:
            count = repo.count_tracked_files(conn)
            if count > 0:
                confirmed = typer.confirm(
                    'This will wipe the existing library'
                    f' ({count} tracked file(s)). Continue?',
                )
                if not confirmed:
                    logger.info('Force re-index aborted by user')
                    raise typer.Abort()

        try:
            with Progress(
                *Progress.get_default_columns(),
                TimeElapsedColumn(),
                console=console,
                transient=True,
            ) as progress:
                stats = scanner_mod.scan_folder(
                    folder,
                    conn,
                    covers_dir,
                    progress,
                    force,
                    backend,
                )
        except NotImplementedError as e:
            logger.error('Tag backend {!r} is not implemented', backend)
            console.print(f'[red]Error:[/red] {e}')
            raise typer.Exit(1) from e

    table = Table(title='Scan complete', show_header=False)
    table.add_column('', style='dim')
    table.add_column('', justify='right')
    table.add_row('Total files', str(stats.total_files))
    table.add_row('New', str(stats.new_files))
    table.add_row('Updated', str(stats.updated_files))
    table.add_row('Deleted', str(stats.deleted_files))
    table.add_row('Skipped', str(stats.skipped_files))
    if stats.errors:
        table.add_row('[red]Errors[/red]', f'[red]{stats.errors}[/red]')
    table.add_row('Elapsed', f'{stats.elapsed_s:.2f}s')
    console.print(table)

    if export_after:
        with open_connection(db_path) as conn:
            _run_export(conn, get_export_path())


@app.command()
def stats() -> None:
    """Show library statistics."""
    db_path = get_db_path()
    if not db_path.exists():
        console.print(
            '[red]No library found. Run [bold]scan[/bold] first.[/red]'
        )
        raise typer.Exit(1)

    with open_connection(db_path) as conn:
        s = repo.query_stats(conn)

    db_size_mb = db_path.stat().st_size / (1024 * 1024)
    console.print(f'Database: {db_path} ({db_size_mb:.1f} MB)')

    table = Table(title='Library', show_header=False)
    table.add_column('', style='dim')
    table.add_column('', justify='right')
    table.add_row('Tracks', str(s.track_count))
    table.add_row('Albums', str(s.album_count))
    table.add_row('Artists', str(s.artist_count))
    table.add_row('Genres', str(s.genre_count))
    table.add_row('Duration', _format_duration(s.total_duration_ms))
    console.print(table)


@app.command()
def export() -> None:
    """Export the library to JSON for the frontend."""
    db_path = get_db_path()
    if not db_path.exists():
        console.print(
            '[red]No library found. Run [bold]scan[/bold] first.[/red]'
        )
        raise typer.Exit(1)

    with open_connection(db_path) as conn:
        _run_export(conn, get_export_path())


def main() -> None:
    _setup_logging()
    try:
        app()
    except KeyboardInterrupt:
        logger.info('Interrupted by user')
        raise SystemExit(130) from None
    except SystemExit as e:
        if e.code not in (0, None):
            logger.warning('Exited with code {}', e.code)
        raise
    except Exception as e:
        logger.exception('Unhandled exception')
        raise SystemExit(1) from e


if __name__ == '__main__':
    main()
