from typing import Any

from mutagen import FileType

class FLACInfo:
    length: float
    bitrate: int

class Picture:
    type: int
    mime: str
    data: bytes
    width: int
    height: int
    depth: int
    colors: int
    def __init__(self, data: bytes = ...) -> None: ...
    def write(self) -> bytes: ...

class FLAC(FileType):
    info: FLACInfo
    pictures: list[Picture]
    def __init__(self, filename: str, **kwargs: Any) -> None: ...
