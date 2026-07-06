import sqlite3
from collections.abc import Iterable
from typing import Self

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class SchemaBaseModel(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        validate_by_name=True,
    )

    @classmethod
    def from_db(cls, row: sqlite3.Row) -> Self:
        return cls.model_validate(dict(row))

    @classmethod
    def from_db_rows(cls, rows: Iterable[sqlite3.Row]) -> list[Self]:
        return [cls.from_db(row) for row in rows]


# ---------------------------------------------------------------------------
# DB row models
# ---------------------------------------------------------------------------


class GenreRow(SchemaBaseModel):
    id: int
    name: str


class ArtistRow(SchemaBaseModel):
    id: int
    name: str


class CoverRow(SchemaBaseModel):
    id: int
    content_hash: str
    extension: str


class AlbumRow(SchemaBaseModel):
    id: int
    title: str
    artist_id: int
    year: int | None
    genre_id: int
    cover_id: int | None


class AlbumQueryRow(SchemaBaseModel):
    id: int
    title: str
    artist_id: int
    year: int | None
    genre_id: int
    is_compilation: bool
    content_hash: str | None
    extension: str | None
    track_count: int


class TrackRow(SchemaBaseModel):
    id: int
    title: str | None
    artist_id: int
    album_id: int
    track_number: int | None
    disc_number: int | None
    duration_ms: int
    path: str


class FileStateRow(SchemaBaseModel):
    path: str
    size_bytes: int
    change_time_ns: int


# ---------------------------------------------------------------------------
# Export models (serialized to export.json with camelCase keys)
# ---------------------------------------------------------------------------


class ExportStats(SchemaBaseModel):
    track_count: int
    album_count: int
    artist_count: int
    genre_count: int
    total_duration_ms: int


class ExportArtist(SchemaBaseModel):
    id: int
    name: str
    album_ids: list[int]


class ExportAlbum(SchemaBaseModel):
    id: int
    title: str
    year: int | None
    artist_id: int
    genre_id: int
    is_compilation: bool
    track_count: int
    track_ids: list[int]
    cover: str | None


class ExportTrack(SchemaBaseModel):
    id: int
    title: str | None
    artist_id: int
    album_id: int
    track_number: int | None
    disc_number: int | None
    duration_ms: int
    bitrate_kbps: int | None
    audio_format: str | None
    path: str


class ExportGenre(SchemaBaseModel):
    id: int
    name: str
    album_ids: list[int]


class ExportPayload(SchemaBaseModel):
    stats: ExportStats
    artists: list[ExportArtist]
    albums: list[ExportAlbum]
    tracks: list[ExportTrack]
    genres: list[ExportGenre]


# ---------------------------------------------------------------------------
# Internal processing models
# ---------------------------------------------------------------------------


class RawTags(BaseModel):
    path: str
    title: str | None
    track_number: int | None
    disc_number: int | None
    duration_ms: int
    track_artist: str | None
    album_artist: str | None
    album_title: str | None
    year: int | None
    genre: str | None
    cover_bytes: bytes | None
    cover_format: str | None
    # Technical metadata
    bitrate_kbps: int | None = None
    audio_format: str | None = None


class ScanStats(BaseModel):
    total_files: int = 0
    new_files: int = 0
    updated_files: int = 0
    deleted_files: int = 0
    skipped_files: int = 0
    errors: int = 0
    elapsed_s: float = 0.0
