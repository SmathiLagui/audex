from typing import Any

class MP4Info:
    length: float
    bitrate: int

class MP4Cover(bytes):
    imageformat: int
    FORMAT_JPEG: int
    FORMAT_PNG: int
    def __new__(cls, data: bytes, imageformat: int = ...) -> MP4Cover: ...

class MP4Tags:
    def get(self, key: str, default: Any = ...) -> Any: ...
    def __getitem__(self, key: str) -> Any: ...
    def __contains__(self, key: str) -> bool: ...

class MP4:
    info: MP4Info
    tags: MP4Tags | None
    def __init__(self, filename: str, **kwargs: Any) -> None: ...
