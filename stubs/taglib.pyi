from pathlib import Path
from types import TracebackType
from typing import Self

class Picture:
    picture_type: str
    mime_type: str
    # Typed as bytes by the real taglib.cp*.pyd binding, but it's a
    # compiled C extension we don't control - don't trust that promise
    # at runtime, treat it as possibly None.
    data: bytes | None

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
