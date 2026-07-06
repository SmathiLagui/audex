from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from loguru import logger

from ..models import RawTags
from .helpers import TagReadError
from .mutagen import read_mutagen
from .pytaglib import read_pytaglib

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


class TagBackend(StrEnum):
    PyTagLib = 'pytaglib'
    Mutagen = 'mutagen'


def is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def resolve_reader(backend: TagBackend) -> Callable[[Path], RawTags]:
    match backend:
        case TagBackend.PyTagLib:
            return read_pytaglib
        case TagBackend.Mutagen:
            return read_mutagen
        case _:
            raise NotImplementedError(
                f'Tag backend {backend!r} is not implemented'
            )


def read_tags(
    path: Path,
    backend: TagBackend = TagBackend.PyTagLib,
) -> RawTags | None:
    reader = resolve_reader(backend)
    try:
        return reader(path)
    except TagReadError:
        logger.exception('Failed to read tags from {}', path)
    return None
