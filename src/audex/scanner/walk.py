import os
from pathlib import Path

from .. import tags as tags_mod


def walk_audio(folder: Path) -> list[Path]:
    """Return all audio files under *folder*, sorted by path."""
    result: list[Path] = []
    for dirpath, _, filenames in os.walk(str(folder)):
        for name in filenames:
            if Path(name).suffix.lower() not in tags_mod.AUDIO_EXTENSIONS:
                continue

            result.append(Path(dirpath) / name)
    result.sort()
    return result
