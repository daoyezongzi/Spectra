from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class NormalizeResult:
    final_genre: str
    final_subgenre: str
    language: str
    mood: str
    scene: str
    theme: str
    confidence: float
    decision_source: str
    reason: str


class GenreNormalizer:
    def __init__(self, taxonomy_path: Path, tag_rule_path: Path) -> None:
        payload = self._load_taxonomy(taxonomy_path)
        rule_payload = self._load_taxonomy(tag_rule_path)
        self.taxonomy_version: str = payload["taxonomy_version"]
        self.genres: list[str] = [x["name"] for x in payload["genres"]]
        self.keyword_map: dict[str, list[str]] = {
            x["name"]: [k.lower() for k in x["keywords"]] for x in payload["genres"]
        }
        self.subgenres: list[str] = [str(x["name"]) for x in rule_payload.get("subgenre_rules", [])]
        self.tag_rule_version: str = str(rule_payload.get("rule_version", "tag_rules_v1"))
        self.genre_alias_map: dict[str, list[str]] = {
            genre: [str(x).strip().lower() for x in aliases if str(x).strip()]
            for genre, aliases in rule_payload.get("genre_alias", {}).items()
        }
        self.dimension_alias_map: dict[str, dict[str, list[str]]] = {}
        for dimension, candidates in rule_payload.get("dimensions", {}).items():
            mapped: dict[str, list[str]] = {}
            for label, aliases in candidates.items():
                mapped[str(label)] = [str(x).strip().lower() for x in aliases if str(x).strip()]
            self.dimension_alias_map[str(dimension)] = mapped
        self.subgenre_rules: list[dict[str, Any]] = list(rule_payload.get("subgenre_rules", []))

        self.alias_to_genre: dict[str, str] = {}
        for genre, keywords in self.keyword_map.items():
            for keyword in keywords:
                self.alias_to_genre[keyword] = genre
            self.alias_to_genre[genre.lower()] = genre
        for genre, aliases in self.genre_alias_map.items():
            for alias in aliases:
                self.alias_to_genre[alias] = genre

    def _load_taxonomy(self, taxonomy_path: Path) -> dict[str, Any]:
        with taxonomy_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload

    def normalize(
        self,
        song_name: str,
        artist: str,
        wiki_style: str | None,
        raw_tags: list[str],
    ) -> NormalizeResult:
        del song_name, artist
        cleaned_tags = [x.strip().lower() for x in raw_tags if x.strip()]
        language = self._pick_dimension_label("language", cleaned_tags)
        mood = self._pick_dimension_label("mood", cleaned_tags)
        scene = self._pick_dimension_label("scene", cleaned_tags)
        theme = self._pick_dimension_label("theme", cleaned_tags)

        fallback_reason = "No genre keyword hit in raw tags."
        decision_source = "L2/tag_rules"
        if wiki_style:
            direct = self.map_wiki_style(wiki_style)
            if direct:
                subgenre = self._resolve_subgenre(direct, language, mood, cleaned_tags)
                return NormalizeResult(
                    final_genre=direct,
                    final_subgenre=subgenre,
                    language=language,
                    mood=mood,
                    scene=scene,
                    theme=theme,
                    confidence=0.95,
                    decision_source="L1/wiki",
                    reason=f"Matched wiki style '{wiki_style}', rule={subgenre}.",
                )

        scores: dict[str, int] = {genre: 0 for genre in self.genres}
        for tag in cleaned_tags:
            for alias, genre in self.alias_to_genre.items():
                if alias and alias in tag:
                    scores[genre] += 1

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_genre, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0

        if top_score == 0:
            subgenre = self._resolve_subgenre("Other", language, mood, cleaned_tags)
            return NormalizeResult(
                final_genre="Other",
                final_subgenre=subgenre,
                language=language,
                mood=mood,
                scene=scene,
                theme=theme,
                confidence=0.35,
                decision_source=decision_source,
                reason=fallback_reason,
            )
        if top_score == second_score:
            # 中文注释：并列冲突时不直接丢到 Other，而是保留第一候选主风格，降置信度并要求复核。
            subgenre = self._resolve_subgenre(top_genre, language, mood, cleaned_tags)
            return NormalizeResult(
                final_genre=top_genre,
                final_subgenre=subgenre,
                language=language,
                mood=mood,
                scene=scene,
                theme=theme,
                confidence=0.5,
                decision_source=decision_source,
                reason=f"Multi-genre tie; fallback to top genre '{top_genre}', review required.",
            )

        confidence = min(0.90, 0.55 + 0.1 * top_score)
        subgenre = self._resolve_subgenre(top_genre, language, mood, cleaned_tags)
        return NormalizeResult(
            final_genre=top_genre,
            final_subgenre=subgenre,
            language=language,
            mood=mood,
            scene=scene,
            theme=theme,
            confidence=round(confidence, 2),
            decision_source=decision_source,
            reason=f"Tag score={top_score}, rule={subgenre}.",
        )

    # 中文注释：从指定维度里选分数最高的标签，作为该维度的标准输出。
    def _pick_dimension_label(self, dimension: str, cleaned_tags: list[str]) -> str:
        candidates = self.dimension_alias_map.get(dimension, {})
        if not candidates:
            return "Unknown"
        score_map: dict[str, int] = {label: 0 for label in candidates}
        for tag in cleaned_tags:
            for label, aliases in candidates.items():
                for alias in aliases:
                    if alias and alias in tag:
                        score_map[label] += 1
        ranked = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
        if not ranked or ranked[0][1] == 0:
            return "Unknown"
        return ranked[0][0]

    # 中文注释：按规则组合二级标签，规则未命中时回退到可解释的默认值。
    def _resolve_subgenre(
        self,
        final_genre: str,
        language: str,
        mood: str,
        cleaned_tags: list[str],
    ) -> str:
        for rule in self.subgenre_rules:
            rule_name = str(rule.get("name", "")).strip()
            if not rule_name:
                continue
            if str(rule.get("if_genre", "")).strip() and str(rule.get("if_genre")) != final_genre:
                continue
            if str(rule.get("if_language", "")).strip() and str(rule.get("if_language")) != language:
                continue
            if str(rule.get("if_mood", "")).strip() and str(rule.get("if_mood")) != mood:
                continue
            any_tags = [str(x).strip().lower() for x in rule.get("any_tags", []) if str(x).strip()]
            if any_tags and not any(alias in tag for tag in cleaned_tags for alias in any_tags):
                continue
            return rule_name

        if final_genre == "Pop":
            if language != "Unknown" and mood != "Unknown":
                return f"{language}流行/{mood}"
            if language != "Unknown":
                return f"{language}流行"
            if mood != "Unknown":
                return f"{mood}流行"
            return "流行"
        if final_genre in {"Rock", "Electronic"} and mood != "Unknown":
            return f"{mood}{final_genre}"
        return final_genre if final_genre != "Other" else "Other"

    def _alias_lookup(self, value: str) -> str | None:
        lower = value.strip().lower()
        if not lower:
            return None
        if lower in self.alias_to_genre:
            return self.alias_to_genre[lower]
        for alias, genre in self.alias_to_genre.items():
            if alias in lower:
                return genre
        return None

    def map_wiki_style(self, wiki_style: str | None) -> str | None:
        if not wiki_style:
            return None
        return self._alias_lookup(wiki_style)

