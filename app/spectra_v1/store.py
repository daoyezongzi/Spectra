from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ProcessedStore:
    def __init__(self, processed_path: Path) -> None:
        self.processed_path = processed_path

    def load(self) -> dict[str, Any]:
        if not self.processed_path.exists():
            return {"items": []}
        with self.processed_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, payload: dict[str, Any]) -> None:
        self.processed_path.parent.mkdir(parents=True, exist_ok=True)
        with self.processed_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def list_processed_ids(self, taxonomy_version: str) -> set[int]:
        payload = self.load()
        processed: set[int] = set()
        for item in payload.get("items", []):
            if item.get("taxonomy_version") == taxonomy_version:
                processed.add(int(item["song_id"]))
        return processed

    def upsert(self, records: list[dict[str, Any]]) -> int:
        payload = self.load()
        merged: dict[tuple[int, str], dict[str, Any]] = {}

        for item in payload.get("items", []):
            key = (int(item["song_id"]), str(item["taxonomy_version"]))
            merged[key] = item

        now = datetime.now(UTC).isoformat()
        for record in records:
            key = (int(record["song_id"]), str(record["taxonomy_version"]))
            merged[key] = {
                "song_id": int(record["song_id"]),
                "song_name": record["song_name"],
                "artist": record["artist"],
                "final_genre": record["final_genre"],
                "final_subgenre": record.get("final_subgenre", ""),
                "language": record.get("language", ""),
                "mood": record.get("mood", ""),
                "scene": record.get("scene", ""),
                "theme": record.get("theme", ""),
                "taxonomy_version": record["taxonomy_version"],
                "updated_at": now,
            }

        payload["items"] = sorted(
            merged.values(),
            key=lambda x: (x["taxonomy_version"], x["song_id"]),
        )
        self._write(payload)
        return len(records)
