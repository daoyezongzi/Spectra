from __future__ import annotations

from typing import Any

from spectra_v1.types import SongEvidence, Track


class MockNeteaseClient:
    """Local mock provider for MVP flow validation."""

    def __init__(self) -> None:
        self._playlists: dict[str, dict[str, Any]] = {
            "pl_001": {
                "playlist_name": "巨型混合歌单 A",
                "track_ids": [101, 102, 103, 104, 105, 106, 107, 108],
            },
            "pl_002": {
                "playlist_name": "巨型混合歌单 B",
                "track_ids": [109, 110, 111, 112, 113, 114, 115, 116],
            },
        }
        self._songs: dict[int, dict[str, Any]] = {
            101: {
                "song_name": "Night Drive",
                "artist": "Luna Wave",
                "wiki_style": "City Pop",
                "similar_playlist_tags": ["夜晚", "日系", "复古", "citypop"],
            },
            102: {
                "song_name": "Heatline",
                "artist": "Rye & Neo",
                "wiki_style": "R&B",
                "similar_playlist_tags": ["RNB", "性感", "律动", "慢摇"],
            },
            103: {
                "song_name": "Binary Pulse",
                "artist": "Kilo Volt",
                "wiki_style": None,
                "similar_playlist_tags": ["EDM", "电子", "抖腿", "house", "festival"],
            },
            104: {
                "song_name": "String Garden",
                "artist": "Mio Quartet",
                "wiki_style": "Classical",
                "similar_playlist_tags": ["弦乐", "钢琴", "古典", "学习"],
            },
            105: {
                "song_name": "Street Cipher",
                "artist": "Rooftop Crew",
                "wiki_style": None,
                "similar_playlist_tags": ["hiphop", "说唱", "flow", "街头"],
            },
            106: {
                "song_name": "Cloud Waltz",
                "artist": "Ari Vale",
                "wiki_style": None,
                "similar_playlist_tags": ["古风", "器乐", "纯音乐", "平静"],
            },
            107: {
                "song_name": "Velvet Room",
                "artist": "The Amber Set",
                "wiki_style": None,
                "similar_playlist_tags": ["jazz", "酒吧", "萨克斯", "夜晚"],
            },
            108: {
                "song_name": "Sparks",
                "artist": "North Engine",
                "wiki_style": "Rock",
                "similar_playlist_tags": ["摇滚", "吉他", "live", "热血"],
            },
            109: {
                "song_name": "Bloom in Rain",
                "artist": "Paper Field",
                "wiki_style": "Folk",
                "similar_playlist_tags": ["民谣", "木吉他", "清新", "旅途"],
            },
            110: {
                "song_name": "No Signal",
                "artist": "Raster",
                "wiki_style": None,
                "similar_playlist_tags": ["ambient", "纯音乐", "氛围", "冥想"],
            },
            111: {
                "song_name": "Neon Skyline",
                "artist": "Skyframe",
                "wiki_style": None,
                "similar_playlist_tags": ["synthwave", "电子", "复古", "夜跑"],
            },
            112: {
                "song_name": "Blue Harbor",
                "artist": "Gina Malloy",
                "wiki_style": "Jazz",
                "similar_playlist_tags": ["jazz", "钢琴", "慵懒", "晚间"],
            },
            113: {
                "song_name": "Palm Route",
                "artist": "MKT-82",
                "wiki_style": "City Pop",
                "similar_playlist_tags": ["citypop", "复古", "海风", "日系"],
            },
            114: {
                "song_name": "After Class",
                "artist": "Nori",
                "wiki_style": None,
                "similar_playlist_tags": ["动漫", "j-pop", "青春", "轻快"],
            },
            115: {
                "song_name": "Monument",
                "artist": "Kappa Orchestra",
                "wiki_style": "Classical",
                "similar_playlist_tags": ["管弦", "史诗", "交响乐", "纯音乐"],
            },
            116: {
                "song_name": "Sleep Cycle",
                "artist": "Murmur",
                "wiki_style": None,
                "similar_playlist_tags": ["深夜", "白噪音", "助眠", "氛围"],
            },
        }

    def generate_qr_payload(self) -> dict[str, str]:
        return {
            "qr_key": "mock-qr-key",
            "qr_url": "https://music.163.com/login?codekey=mock-qr-key",
            "qr_img_data_uri": "",
        }

    def authorize_with_qr(self, qr_key: str) -> dict[str, str]:
        if qr_key != "mock-qr-key":
            raise ValueError("Invalid mock qr_key.")
        return {
            "status": "success",
            "message": "Mock login success.",
            "cookie": "MUSIC_U=mock_cookie",
            "uid": "mock_uid_001",
        }

    def list_playlists(self, cookie: str) -> list[dict[str, Any]]:
        if not cookie:
            raise ValueError("Cookie required.")
        rows: list[dict[str, Any]] = []
        for playlist_id, playlist in self._playlists.items():
            rows.append(
                {
                    "playlist_id": playlist_id,
                    "playlist_name": playlist["playlist_name"],
                    "track_count": len(playlist["track_ids"]),
                }
            )
        return rows

    def get_playlist_tracks(self, playlist_id: str, cookie: str) -> list[Track]:
        if not cookie:
            raise ValueError("Cookie required.")
        track_ids = self._playlists[playlist_id]["track_ids"]
        rows: list[Track] = []
        for song_id in track_ids:
            song = self._songs[song_id]
            rows.append(
                Track(
                    song_id=song_id,
                    song_name=song["song_name"],
                    artist=song["artist"],
                )
            )
        return rows

    def fetch_song_evidence(self, song_id: int, cookie: str) -> SongEvidence:
        if not cookie:
            raise ValueError("Cookie required.")
        song = self._songs[song_id]
        return SongEvidence(
            wiki_style=song["wiki_style"],
            similar_playlist_tags=list(song["similar_playlist_tags"]),
        )

    def batch_distribute(
        self,
        playlist_name_prefix: str,
        genre_to_song_ids: dict[str, list[int]],
        cookie: str,
    ) -> list[dict[str, Any]]:
        if not cookie:
            raise ValueError("Cookie required.")
        reports: list[dict[str, Any]] = []
        for genre, song_ids in genre_to_song_ids.items():
            target_name = f"{playlist_name_prefix} {genre} 归档"
            playlist_id = self.create_playlist(target_name, cookie)
            self.add_tracks_to_playlist(playlist_id, song_ids, cookie)
            reports.append(
                {
                    "target_playlist": target_name,
                    "song_count": len(song_ids),
                    "song_ids": song_ids,
                }
            )
        return reports

    def create_playlist(self, playlist_name: str, cookie: str) -> str:
        if not cookie:
            raise ValueError("Cookie required.")
        if any(x["playlist_name"] == playlist_name for x in self._playlists.values()):
            raise RuntimeError(f"创建歌单失败: duplicate playlist name '{playlist_name}'")
        new_id = f"pl_auto_{len(self._playlists) + 1:03d}"
        self._playlists[new_id] = {"playlist_name": playlist_name, "track_ids": []}
        return new_id

    def add_tracks_to_playlist(self, playlist_id: str, song_ids: list[int], cookie: str) -> None:
        if not cookie:
            raise ValueError("Cookie required.")
        if playlist_id not in self._playlists:
            raise RuntimeError(f"Playlist not found: {playlist_id}")
        exists = set(self._playlists[playlist_id]["track_ids"])
        pending = [int(x) for x in song_ids if int(x) not in exists]
        self._playlists[playlist_id]["track_ids"].extend(pending)
