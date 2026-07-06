from pathlib import Path
from typing import Any

from mutagen import FileType
from mutagen.id3 import ID3

class MP3Info:
    length: float
    bitrate: int

class MP3(FileType):
    info: MP3Info
    tags: ID3 | None
    def __init__(self, filename: str | Path, **kwargs: Any) -> None: ...
