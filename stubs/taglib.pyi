from pathlib import Path
from types import TracebackType
from typing import Self

class Picture:
    picture_type: str
    mime_type: str
    data: bytes

class File:
    tags: dict[str, list[str]]
    pictures: list[Picture]
    length: float
    bitrate: int
    def __init__(self, path: str | Path) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...
