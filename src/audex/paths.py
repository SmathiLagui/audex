from pathlib import Path


def get_app_dir() -> Path:
    app_dir = Path.home() / 'AppData' / 'Roaming' / 'ng-player'
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_db_path() -> Path:
    return get_app_dir() / 'library.db'


def get_export_path() -> Path:
    return get_app_dir() / 'export.json'


def get_covers_dir() -> Path:
    covers = get_app_dir() / 'covers'
    covers.mkdir(parents=True, exist_ok=True)
    return covers


def get_logs_dir() -> Path:
    logs = get_app_dir() / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    return logs
