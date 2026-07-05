import logging
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
from . import scanner as scanner_mod
from .database import (
    create_schema,
    get_app_dir,
    get_covers_dir,
    get_db_path,
    get_logs_dir,
    open_connection,
)
from .tags import TagBackend

app = typer.Typer(add_completion=False)
console = Console()


def _prune_old_logs(logs_dir: Path) -> None:
    # Keep the last 10 log files
    keep = 9
    files = sorted(
        logs_dir.glob('scanner_*.log'),
        key=lambda f: f.stat().st_mtime,
    )
    for old in files[:-keep] if len(files) >= keep else []:
        old.unlink(missing_ok=True)


def _setup_logging() -> None:
    logger.remove()
    logs_dir = get_logs_dir()
    _prune_old_logs(logs_dir)
    logger.add(
        logs_dir / 'scanner_{time}.log',
        level=logging.DEBUG,
        encoding='utf-8',
        delay=True,
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
    backend: Annotated[
        TagBackend,
        typer.Option(
            '--backend',
            '-b',
            help='Tag reading backend.',
        ),
    ] = TagBackend.PyTagLib,
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


@app.command()
def export() -> None:
    """Export the library to JSON for the frontend."""
    db_path = get_db_path()
    if not db_path.exists():
        console.print(
            '[red]No library found. Run [bold]scan[/bold] first.[/red]'
        )
        raise typer.Exit(1)

    app_dir = get_app_dir()
    covers_dir = get_covers_dir()

    t0 = time.perf_counter()
    with (
        open_connection(db_path) as conn,
        console.status('Exporting library...'),
    ):
        out_path = export_mod.export_library(conn, app_dir, covers_dir)
    elapsed = time.perf_counter() - t0

    console.print(f'Exported to [bold]{out_path}[/bold] in {elapsed:.2f}s')


def main() -> None:
    _setup_logging()
    app()


if __name__ == '__main__':
    main()
