from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Track:
    song_id: int
    song_name: str
    artist: str


@dataclass(frozen=True)
class SongEvidence:
    wiki_style: str | None
    similar_playlist_tags: list[str]

