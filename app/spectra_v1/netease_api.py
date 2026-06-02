from __future__ import annotations

import re
import time
from typing import Any

import requests

from spectra_v1.types import SongEvidence, Track


class NeteaseApiClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: int = 12,
        initial_uid: str = "",
        request_retries: int = 2,
        retry_interval_seconds: float = 1.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._uid: str = initial_uid.strip()
        self.request_retries = max(0, request_retries)
        self.retry_interval_seconds = max(0.0, retry_interval_seconds)

    def set_uid(self, uid: str) -> None:
        self._uid = uid.strip()

    def generate_qr_payload(self) -> dict[str, str]:
        key_resp = self._get("/login/qr/key")
        qr_key = str(key_resp.get("data", {}).get("unikey", ""))
        if not qr_key:
            raise RuntimeError("无法获取二维码 key。")

        create_resp = self._get(
            "/login/qr/create",
            {"key": qr_key, "qrimg": "true"},
        )
        data = create_resp.get("data", {})
        qr_url = str(data.get("qrurl", ""))
        qr_img_data_uri = str(data.get("qrimg", ""))
        return {
            "qr_key": qr_key,
            "qr_url": qr_url,
            "qr_img_data_uri": qr_img_data_uri,
        }

    def authorize_with_qr(self, qr_key: str) -> dict[str, str]:
        resp = self._get("/login/qr/check", {"key": qr_key})
        code = int(resp.get("code", -1))

        if code == 801:
            return {"status": "wait_scan", "message": "等待扫码。"}
        if code == 802:
            return {"status": "wait_confirm", "message": "已扫码，等待手机确认。"}
        if code == 800:
            return {"status": "expired", "message": "二维码已过期，请重新生成。"}
        if code != 803:
            message = str(resp.get("message", "扫码登录失败。"))
            return {"status": "error", "message": message}

        cookie = str(resp.get("cookie", "")).strip()
        if not cookie:
            return {"status": "error", "message": "登录成功但未返回 cookie。"}

        uid = self._fetch_uid(cookie)
        self._uid = uid
        return {
            "status": "success",
            "message": "登录成功。",
            "cookie": cookie,
            "uid": uid,
        }

    def list_playlists(self, cookie: str) -> list[dict[str, Any]]:
        uid = self._uid or self._fetch_uid(cookie)
        self._uid = uid
        # 分页拉全量歌单，避免固定 1000 上限截断。
        playlists: list[dict[str, Any]] = []
        offset = 0
        page_size = 1000
        while True:
            resp = self._get(
                "/user/playlist",
                {"uid": uid, "limit": page_size, "offset": offset},
                cookie=cookie,
            )
            chunk = resp.get("playlist", [])
            if not chunk:
                break
            playlists.extend(chunk)
            if len(chunk) < page_size:
                break
            offset += page_size

        rows: list[dict[str, Any]] = []
        for item in playlists:
            playlist_id = item.get("id")
            if playlist_id is None:
                continue
            rows.append(
                {
                    "playlist_id": str(playlist_id),
                    "playlist_name": str(item.get("name", "")),
                    "track_count": int(item.get("trackCount", 0)),
                }
            )
        return rows

    def get_playlist_tracks(self, playlist_id: str, cookie: str) -> list[Track]:
        # 分页拉全量歌曲，避免超过 1000 首后被截断。
        songs: list[dict[str, Any]] = []
        offset = 0
        page_size = 1000
        while True:
            resp = self._get(
                "/playlist/track/all",
                {"id": playlist_id, "limit": page_size, "offset": offset},
                cookie=cookie,
            )
            chunk = resp.get("songs", [])
            if not chunk:
                break
            songs.extend(chunk)
            if len(chunk) < page_size:
                break
            offset += page_size

        rows: list[Track] = []
        for song in songs:
            song_id = song.get("id")
            if song_id is None:
                continue
            artists = song.get("ar") or []
            artist_name = " / ".join(str(a.get("name", "")) for a in artists if a.get("name"))
            rows.append(
                Track(
                    song_id=int(song_id),
                    song_name=str(song.get("name", "")),
                    artist=artist_name or "Unknown",
                )
            )
        return rows

    def fetch_song_evidence(self, song_id: int, cookie: str) -> SongEvidence:
        wiki_style = self._fetch_wiki_style(song_id, cookie)
        tags = self._fetch_similar_playlist_tags(song_id, cookie)
        return SongEvidence(
            wiki_style=wiki_style,
            similar_playlist_tags=tags,
        )

    def batch_distribute(
        self,
        playlist_name_prefix: str,
        genre_to_song_ids: dict[str, list[int]],
        cookie: str,
    ) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for genre, song_ids in genre_to_song_ids.items():
            if not song_ids:
                continue
            target_name = f"{playlist_name_prefix} {genre} 归档"
            target_playlist_id = self.create_playlist(target_name, cookie)
            self.add_tracks_to_playlist(str(target_playlist_id), song_ids, cookie)
            reports.append(
                {
                    "target_playlist": target_name,
                    "song_count": len(song_ids),
                    "song_ids": song_ids,
                }
            )
        return reports

    def create_playlist(self, playlist_name: str, cookie: str) -> str:
        payload = {"name": playlist_name}
        try:
            created = self._post("/playlist/create", payload, cookie=cookie)
        except RuntimeError as exc:
            if "HTTP 405" not in str(exc):
                raise
            # 兼容少数 API 服务端仅支持 GET 的场景。
            created = self._get("/playlist/create", payload, cookie=cookie)
        playlist = created.get("playlist", {})
        playlist_id = playlist.get("id")
        if playlist_id is None:
            raise RuntimeError(f"创建歌单失败: {playlist_name}")
        return str(playlist_id)

    def add_tracks_to_playlist(self, playlist_id: str, song_ids: list[int], cookie: str) -> None:
        if not song_ids:
            return
        song_param = ",".join(str(x) for x in song_ids)
        payload = {"op": "add", "pid": str(playlist_id), "tracks": song_param}
        try:
            self._post("/playlist/tracks", payload, cookie=cookie)
        except RuntimeError as exc:
            if "HTTP 405" not in str(exc):
                raise
            # 兼容少数 API 服务端仅支持 GET 的场景。
            self._get("/playlist/tracks", payload, cookie=cookie)

    def _fetch_uid(self, cookie: str) -> str:
        resp = self._get("/login/status", cookie=cookie)
        account = resp.get("data", {}).get("account", {})
        uid = account.get("id")
        if uid is None:
            raise RuntimeError("无法从登录状态中获取 uid。")
        return str(uid)

    def _fetch_wiki_style(self, song_id: int, cookie: str) -> str | None:
        endpoints = [
            "/song/wiki/summary",
            "/song/wiki/whole",
        ]
        for endpoint in endpoints:
            try:
                resp = self._get(endpoint, {"id": song_id}, cookie=cookie)
            except RuntimeError:
                continue
            parsed = self._extract_style_from_payload(resp)
            if parsed:
                return parsed
        return None

    def _extract_style_from_payload(self, payload: dict[str, Any]) -> str | None:
        values: list[str] = []

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    key_lower = str(key).lower()
                    if key_lower in {"style", "曲风", "genre", "genres"}:
                        if isinstance(item, str):
                            values.append(item)
                        elif isinstance(item, list):
                            for x in item:
                                if isinstance(x, str):
                                    values.append(x)
                                elif isinstance(x, dict):
                                    name = x.get("name")
                                    if isinstance(name, str):
                                        values.append(name)
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(payload)
        cleaned = [x.strip() for x in values if x and x.strip()]
        if not cleaned:
            return None
        return cleaned[0]

    def _fetch_similar_playlist_tags(self, song_id: int, cookie: str) -> list[str]:
        resp = self._get("/simi/playlist", {"id": song_id}, cookie=cookie)
        playlists = resp.get("playlists", [])
        tags: list[str] = []
        for playlist in playlists:
            raw = playlist.get("tags")
            if isinstance(raw, str):
                parts = [x.strip() for x in raw.replace("，", ",").split(",")]
                tags.extend([x for x in parts if x])
            elif isinstance(raw, list):
                tags.extend([str(x).strip() for x in raw if str(x).strip()])
        return tags

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        cookie: str = "",
    ) -> dict[str, Any]:
        return self._request("GET", path, params=params, cookie=cookie)

    def _post(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        cookie: str = "",
    ) -> dict[str, Any]:
        return self._request("POST", path, params=params, cookie=cookie)

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        cookie: str = "",
    ) -> dict[str, Any]:
        merged = dict(params or {})
        merged["timestamp"] = int(time.time() * 1000)
        if cookie:
            merged["cookie"] = cookie
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(self.request_retries + 1):
            try:
                if method.upper() == "POST":
                    resp = requests.post(url, data=merged, timeout=self.timeout_seconds)
                else:
                    resp = requests.get(url, params=merged, timeout=self.timeout_seconds)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.request_retries:
                    time.sleep(self.retry_interval_seconds)
                    continue
                detail = self._mask_sensitive_text(str(exc))
                # 仅暴露 endpoint path，不回显带参数 URL，避免泄露 cookie/token。
                raise RuntimeError(f"Netease API 请求失败: {path} ({detail})") from exc

            # 5xx 常见于 API 进程瞬断，做短重试。
            if resp.status_code >= 500 and attempt < self.request_retries:
                time.sleep(self.retry_interval_seconds)
                continue
            if resp.status_code >= 400:
                raise RuntimeError(f"Netease API HTTP {resp.status_code}: {path}")
            try:
                return resp.json()
            except ValueError as exc:
                raise RuntimeError(f"Netease API 返回非 JSON: {path}") from exc

        if last_exc is not None:
            detail = self._mask_sensitive_text(str(last_exc))
            raise RuntimeError(f"Netease API 请求失败: {path} ({detail})") from last_exc
        raise RuntimeError(f"Netease API 请求失败: {path} (unknown error)")

    def _mask_sensitive_text(self, text: str) -> str:
        # 屏蔽 query 参数中的 cookie=...（常见于 requests 异常消息）
        masked = re.sub(r"(?i)([?&]cookie=)[^&\s)]+", r"\1***", text)
        # 屏蔽常见网易云登录字段
        masked = re.sub(r"(?i)(MUSIC_[A-Z_]+)=([^;,\s)]+)", r"\1=***", masked)
        masked = re.sub(r"(?i)(__csrf=)([^;,\s)]+)", r"\1***", masked)
        return masked
