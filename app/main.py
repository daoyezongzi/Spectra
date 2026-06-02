from __future__ import annotations

import base64
import os
import re
import subprocess
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st

from spectra_v1.config import (
    DEFAULT_NETEASE_API_BASE_URL,
    DEFAULT_NETEASE_API_PORT,
    SpectraConfig,
    load_config,
)
from spectra_v1.netease_api import NeteaseApiClient
from spectra_v1.normalization import GenreNormalizer
from spectra_v1.store import ProcessedStore


ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_PATH = ROOT / "docs" / "taxonomy_v1.json"
TAG_RULES_PATH = ROOT / "docs" / "tag_rules_v1.json"
PROCESSED_PATH = ROOT / "data" / "processed.json"
ENV_PATH = ROOT / ".env"
DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.65
DEFAULT_EXTRACT_SLEEP_MS = 150
DEFAULT_EXTRACT_BATCH_SIZE = 20
RAW_TAG_LIMIT = 60
LOGIN_ENV_KEYS = {"NETEASE_COOKIE", "NETEASE_UID"}
OBSOLETE_ENV_KEYS = {"SPECTRA_SOURCE_MODE", "SPECTRA_AUTO_SAVE_LOGIN", "NETEASE_API_BASE_URL"}

SINGLE_PLAYLIST_GROUPS: list[tuple[str, str]] = [
    ("raw_tags", "原始标签"),
    ("language", "语种"),
    ("final_genre", "一级类目"),
    ("final_subgenre", "二级类目"),
    ("mood", "情绪"),
    ("scene", "场景"),
    ("theme", "主题"),
]
SINGLE_PLAYLIST_DIMENSION_COLUMNS = [
    column for column, _ in SINGLE_PLAYLIST_GROUPS if column != "raw_tags"
]
TRACK_COLUMNS = ["song_id", "song_name", "artist"]
REVIEW_COLUMNS = [
    "song_id",
    "song_name",
    "artist",
    "wiki_style",
    "raw_tags",
    "final_genre",
    "final_subgenre",
    "language",
    "mood",
    "scene",
    "theme",
    "confidence",
    "decision_source",
    "reason",
    "needs_review",
    "review_note",
]


def init_state() -> None:
    defaults: dict[str, object] = {
        "qr_payload": None,
        "cookie": "",
        "uid": "",
        "env_cookie_loaded": False,
        "api_health_ok": None,
        "api_health_message": "",
        "playlists_df": pd.DataFrame(),
        "selected_playlist_id": "",
        "current_playlist_name": "",
        "tracks_df": pd.DataFrame(columns=TRACK_COLUMNS),
        "new_tracks_df": pd.DataFrame(columns=TRACK_COLUMNS),
        "extract_queue": [],
        "extract_rows": [],
        "extract_cursor": 0,
        "extract_total": 0,
        "extract_running": False,
        "extract_paused": False,
        "extract_last_error": "",
        "extract_finished": False,
        "extract_source_signature": "",
        "api_autorestart_done": False,
        "extracted_df": pd.DataFrame(columns=REVIEW_COLUMNS),
        "normalized_df": pd.DataFrame(columns=REVIEW_COLUMNS),
        "review_df": pd.DataFrame(columns=REVIEW_COLUMNS),
        "dist_playlists_df": pd.DataFrame(),
        "single_playlist_source_signature": "",
        "single_playlist_tag_search": "",
        "single_playlist_song_search": "",
        "single_playlist_raw_tags_expanded": False,
        "single_playlist_selected_song_map": {},
    }
    for column, _ in SINGLE_PLAYLIST_GROUPS:
        defaults[single_playlist_filter_key(column)] = []

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def load_dotenv_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    with env_path.open("r", encoding="utf-8") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", maxsplit=1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if key not in LOGIN_ENV_KEYS:
                continue
            if value and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ.setdefault(key, value)


def upsert_env_values(
    env_path: Path,
    updates: dict[str, str],
    remove_keys: set[str] | None = None,
) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    remove_keys = remove_keys or set()
    pending = {key: value for key, value in updates.items()}
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            result.append(line)
            continue
        key, _ = line.split("=", maxsplit=1)
        key = key.strip()
        if key in remove_keys:
            continue
        if key in pending:
            result.append(f"{key}={pending.pop(key)}")
        else:
            result.append(line)

    if result and result[-1] != "":
        result.append("")
    for key, value in pending.items():
        result.append(f"{key}={value}")

    env_path.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8")


def decode_qr_data_uri(qr_data_uri: str) -> bytes | None:
    if not qr_data_uri or "," not in qr_data_uri:
        return None
    _, encoded = qr_data_uri.split(",", maxsplit=1)
    try:
        return base64.b64decode(encoded)
    except ValueError:
        return None


def sanitize_error_message(exc: Exception | str) -> str:
    text = str(exc)
    masked = re.sub(r"(?i)([?&]cookie=)[^&\s)]+", r"\1***", text)
    masked = re.sub(r"(?i)(MUSIC_[A-Z_]+)=([^;,\s)]+)", r"\1=***", masked)
    masked = re.sub(r"(?i)(__csrf=)([^;,\s)]+)", r"\1***", masked)
    return masked


def is_local_api_url(base_url: str) -> bool:
    parsed = urlparse(base_url.strip())
    host = parsed.hostname or ""
    return host in {"127.0.0.1", "localhost"}


def get_api_port(base_url: str) -> int:
    parsed = urlparse(base_url.strip())
    return int(parsed.port or DEFAULT_NETEASE_API_PORT)


def check_api_health(base_url: str, timeout_seconds: int = 3) -> tuple[bool, str]:
    url = base_url.rstrip("/") + "/login/qr/key"
    try:
        response = requests.get(
            url,
            params={"timestamp": int(time.time() * 1000)},
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        return False, sanitize_error_message(exc)
    if response.status_code >= 400:
        return False, f"HTTP {response.status_code}"
    return True, "ok"


def start_local_api_process(base_url: str) -> None:
    detached = getattr(subprocess, "DETACHED_PROCESS", 0)
    new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags = detached | new_group | no_window
    env = os.environ.copy()
    env["PORT"] = str(get_api_port(base_url))
    subprocess.Popen(
        ["cmd.exe", "/c", "npx.cmd --yes NeteaseCloudMusicApi"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def try_recover_local_api(base_url: str) -> tuple[bool, str]:
    if not is_local_api_url(base_url):
        return False, "当前 API 不是本机地址，跳过自动拉起。"
    try:
        ok, message = check_api_health(base_url, timeout_seconds=2)
        if ok:
            return True, "本地 API 已可用。"

        start_local_api_process(base_url)
        for _ in range(12):
            time.sleep(1)
            ok, message = check_api_health(base_url, timeout_seconds=2)
            if ok:
                return True, "本地 API 自动拉起成功。"
        return False, f"本地 API 拉起后仍不可用: {message}"
    except Exception as exc:
        return False, sanitize_error_message(exc)


def dataframe_signature(df: pd.DataFrame) -> str:
    if df.empty or "song_id" not in df.columns:
        return "empty"
    return "|".join(df["song_id"].astype(str).tolist())


def single_playlist_filter_key(column: str) -> str:
    return f"single_playlist_filter_{column}"


def stable_key_fragment(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def to_tracks_dataframe(rows: list[object]) -> pd.DataFrame:
    data = [
        {"song_id": int(item.song_id), "song_name": item.song_name, "artist": item.artist}
        for item in rows
    ]
    return pd.DataFrame(data, columns=TRACK_COLUMNS)


def to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def to_table_tracks(rows: list[dict[str, object]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=REVIEW_COLUMNS)
    df = pd.DataFrame(rows)
    for column in REVIEW_COLUMNS:
        if column not in df.columns:
            df[column] = "" if column != "needs_review" else False
    df["needs_review"] = df["needs_review"].apply(to_bool)
    return df[REVIEW_COLUMNS].reset_index(drop=True)


def get_raw_tag_values(raw_tags_text: object) -> list[str]:
    text = str(raw_tags_text or "")
    if not text.strip():
        return []
    values: list[str] = []
    for part in text.split(","):
        label = part.strip()
        if label and label not in values:
            values.append(label)
    return values


def count_raw_tag_matches(raw_tags_text: object, selected_values: list[str]) -> int:
    raw_tag_set = set(get_raw_tag_values(raw_tags_text))
    return sum(1 for value in selected_values if value in raw_tag_set)


def init_extract_task(source_df: pd.DataFrame) -> None:
    queue_df = source_df[TRACK_COLUMNS].copy()
    st.session_state["extract_queue"] = queue_df.to_dict("records")
    st.session_state["extract_rows"] = []
    st.session_state["extract_cursor"] = 0
    st.session_state["extract_total"] = len(queue_df)
    st.session_state["extract_running"] = False
    st.session_state["extract_paused"] = False
    st.session_state["extract_last_error"] = ""
    st.session_state["extract_finished"] = False
    st.session_state["extract_source_signature"] = dataframe_signature(queue_df)
    st.session_state["api_autorestart_done"] = False
    st.session_state["extracted_df"] = pd.DataFrame(columns=REVIEW_COLUMNS)


def refresh_extracted_df_from_state() -> None:
    st.session_state["extracted_df"] = to_table_tracks(st.session_state["extract_rows"])


def clear_single_playlist_selected_filters(reset_search: bool = False) -> None:
    for column, _ in SINGLE_PLAYLIST_GROUPS:
        st.session_state[single_playlist_filter_key(column)] = []
    if reset_search:
        st.session_state["single_playlist_tag_search"] = ""
        st.session_state["single_playlist_song_search"] = ""
        st.session_state["single_playlist_raw_tags_expanded"] = False


def reset_playlist_workflow_state() -> None:
    st.session_state["extract_queue"] = []
    st.session_state["extract_rows"] = []
    st.session_state["extract_cursor"] = 0
    st.session_state["extract_total"] = 0
    st.session_state["extract_running"] = False
    st.session_state["extract_paused"] = False
    st.session_state["extract_last_error"] = ""
    st.session_state["extract_finished"] = False
    st.session_state["extract_source_signature"] = ""
    st.session_state["api_autorestart_done"] = False
    st.session_state["extracted_df"] = pd.DataFrame(columns=REVIEW_COLUMNS)
    st.session_state["normalized_df"] = pd.DataFrame(columns=REVIEW_COLUMNS)
    st.session_state["review_df"] = pd.DataFrame(columns=REVIEW_COLUMNS)
    st.session_state["single_playlist_selected_song_map"] = {}
    clear_single_playlist_selected_filters(reset_search=True)


def ensure_single_playlist_filter_state(source_df: pd.DataFrame) -> None:
    signature = dataframe_signature(source_df)
    if st.session_state["single_playlist_source_signature"] != signature:
        st.session_state["single_playlist_source_signature"] = signature
        st.session_state["single_playlist_selected_song_map"] = {}
        clear_single_playlist_selected_filters(reset_search=True)


def merge_visible_with_selected(visible_options: list[str], selected_options: list[str]) -> list[str]:
    merged = list(visible_options)
    for value in selected_options:
        if value not in merged:
            merged.append(value)
    return merged


def build_raw_tag_counts(source_df: pd.DataFrame) -> Counter[str]:
    counter: Counter[str] = Counter()
    if source_df.empty:
        return counter
    for raw_tags_text in source_df["raw_tags"].fillna(""):
        counter.update(get_raw_tag_values(raw_tags_text))
    return counter


def build_dimension_options(source_df: pd.DataFrame, column: str) -> list[str]:
    values = [
        str(value).strip()
        for value in source_df[column].fillna("").tolist()
        if str(value).strip() and str(value).strip() != "Unknown"
    ]
    if column == "final_genre":
        values = [value for value in values if value != "Other"]
    counts = Counter(values)
    return [
        value
        for value, _ in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0].lower()),
        )
    ]


def build_single_playlist_tag_options(source_df: pd.DataFrame, tag_search_text: str) -> dict[str, list[str]]:
    query = tag_search_text.strip().lower()
    raw_tag_counts = build_raw_tag_counts(source_df)
    raw_tag_values = [tag for tag, _ in raw_tag_counts.most_common()]
    if query:
        raw_tag_values = [tag for tag in raw_tag_values if query in tag.lower()]
    elif not st.session_state["single_playlist_raw_tags_expanded"]:
        raw_tag_values = raw_tag_values[:RAW_TAG_LIMIT]
    raw_tag_values = merge_visible_with_selected(
        raw_tag_values,
        st.session_state[single_playlist_filter_key("raw_tags")],
    )

    options: dict[str, list[str]] = {"raw_tags": raw_tag_values}
    for column in SINGLE_PLAYLIST_DIMENSION_COLUMNS:
        values = build_dimension_options(source_df, column)
        if query:
            values = [value for value in values if query in value.lower()]
        values = merge_visible_with_selected(values, st.session_state[single_playlist_filter_key(column)])
        options[column] = values
    return options


def apply_single_playlist_tag_filters(source_df: pd.DataFrame) -> pd.DataFrame:
    if source_df.empty:
        return source_df.copy()

    selected_map = {
        column: list(st.session_state[single_playlist_filter_key(column)])
        for column, _ in SINGLE_PLAYLIST_GROUPS
    }
    if not any(selected_map.values()):
        return source_df.iloc[0:0].copy()

    filtered = source_df.copy()
    for column, selected_values in selected_map.items():
        if not selected_values:
            continue
        if column == "raw_tags":
            min_match = 2 if len(selected_values) >= 3 else 1
            filtered["__raw_tag_match_count"] = filtered["raw_tags"].apply(
                lambda text: count_raw_tag_matches(text, selected_values)
            )
            filtered = filtered[filtered["__raw_tag_match_count"] >= min_match]
        else:
            filtered = filtered[filtered[column].fillna("").isin(selected_values)]
    if "__raw_tag_match_count" in filtered.columns:
        filtered = filtered.sort_values(
            by=["__raw_tag_match_count", "song_name"],
            ascending=[False, True],
            kind="stable",
        )
        filtered = filtered.drop(columns=["__raw_tag_match_count"])
    return filtered.reset_index(drop=True)


def selected_filter_count() -> int:
    return sum(len(st.session_state[single_playlist_filter_key(column)]) for column, _ in SINGLE_PLAYLIST_GROUPS)


def render_selected_filter_box() -> None:
    with st.container(border=True):
        left, right = st.columns([0.8, 0.2])
        left.markdown("**已选条件**")
        clear_disabled = selected_filter_count() == 0
        if right.button("清空全部", key="clear_single_playlist_filters", disabled=clear_disabled):
            clear_single_playlist_selected_filters()
            st.rerun()

        if clear_disabled:
            st.caption("还没有选择任何标签。")
            return

        for column, label in SINGLE_PLAYLIST_GROUPS:
            values = list(st.session_state[single_playlist_filter_key(column)])
            if not values:
                continue
            st.markdown(f"**{label}**")
            chunks = [values[index : index + 4] for index in range(0, len(values), 4)]
            for row_index, chunk in enumerate(chunks):
                columns = st.columns(len(chunk))
                for button_index, value in enumerate(chunk):
                    key = f"remove_selected_{column}_{row_index}_{button_index}_{stable_key_fragment(value)}"
                    if columns[button_index].button(f"× {value}", key=key, use_container_width=True):
                        remaining = [item for item in values if item != value]
                        st.session_state[single_playlist_filter_key(column)] = remaining
                        st.rerun()


def render_single_playlist_tag_group(label: str, column: str, options: list[str]) -> None:
    st.markdown(f"**{label}**")
    if not options:
        st.caption("当前没有可选标签。")
        return
    st.pills(
        label=f"选择{label}",
        options=options,
        selection_mode="multi",
        key=single_playlist_filter_key(column),
        label_visibility="collapsed",
    )


def normalize_extracted_df(source_df: pd.DataFrame, normalizer: GenreNormalizer) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for record in source_df.to_dict("records"):
        raw_tags = get_raw_tag_values(record.get("raw_tags", ""))
        result = normalizer.normalize(
            song_name=str(record.get("song_name", "")),
            artist=str(record.get("artist", "")),
            wiki_style=str(record.get("wiki_style", "")).strip() or None,
            raw_tags=raw_tags,
        )
        needs_review = result.confidence < DEFAULT_LOW_CONFIDENCE_THRESHOLD or result.final_genre == "Other"
        rows.append(
            {
                "song_id": int(record["song_id"]),
                "song_name": record["song_name"],
                "artist": record["artist"],
                "wiki_style": record.get("wiki_style", ""),
                "raw_tags": ", ".join(raw_tags),
                "final_genre": result.final_genre,
                "final_subgenre": result.final_subgenre,
                "language": result.language,
                "mood": result.mood,
                "scene": result.scene,
                "theme": result.theme,
                "confidence": float(result.confidence),
                "decision_source": result.decision_source,
                "reason": result.reason,
                "needs_review": needs_review,
                "review_note": result.reason if needs_review else "",
            }
        )
    return to_table_tracks(rows)


def coerce_review_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=REVIEW_COLUMNS)
    result = df.copy()
    for column in REVIEW_COLUMNS:
        if column not in result.columns:
            result[column] = "" if column != "needs_review" else False
    result["needs_review"] = result["needs_review"].apply(to_bool)
    return result[REVIEW_COLUMNS].reset_index(drop=True)


def build_tag_wall_source(review_df: pd.DataFrame) -> pd.DataFrame:
    if review_df.empty:
        return review_df.copy()
    source_df = coerce_review_dataframe(review_df)
    filtered = source_df[source_df["final_genre"].fillna("") != "Other"].copy()
    return filtered.reset_index(drop=True)


def sync_selected_song_map(filtered_df: pd.DataFrame) -> pd.DataFrame:
    selection_map = dict(st.session_state["single_playlist_selected_song_map"])
    result_df = filtered_df.copy()
    result_df["song_id"] = result_df["song_id"].astype(int)
    for song_id in result_df["song_id"].tolist():
        selection_map.setdefault(int(song_id), True)
    result_df["selected"] = result_df["song_id"].map(lambda value: selection_map.get(int(value), True))
    st.session_state["single_playlist_selected_song_map"] = selection_map
    return result_df


def save_selected_song_map(editor_df: pd.DataFrame) -> None:
    selection_map = dict(st.session_state["single_playlist_selected_song_map"])
    for row in editor_df.to_dict("records"):
        selection_map[int(row["song_id"])] = bool(row.get("selected", False))
    st.session_state["single_playlist_selected_song_map"] = selection_map


def get_playlist_name_map(playlists_df: pd.DataFrame) -> dict[str, str]:
    if playlists_df.empty:
        return {}
    return {
        str(row["playlist_id"]): f"{row['playlist_name']} ({int(row['track_count'])} 首)"
        for _, row in playlists_df.iterrows()
    }


def get_client(base_url: str) -> NeteaseApiClient:
    return NeteaseApiClient(
        base_url=base_url,
        initial_uid=str(st.session_state["uid"] or ""),
    )


def process_extract_batch(client: NeteaseApiClient) -> None:
    if not st.session_state["extract_running"] or st.session_state["extract_paused"]:
        return

    queue = list(st.session_state["extract_queue"])
    cursor = int(st.session_state["extract_cursor"])
    total = int(st.session_state["extract_total"])
    if cursor >= total:
        st.session_state["extract_running"] = False
        st.session_state["extract_finished"] = True
        return

    batch_end = min(cursor + DEFAULT_EXTRACT_BATCH_SIZE, total)
    rows = list(st.session_state["extract_rows"])
    sleep_seconds = DEFAULT_EXTRACT_SLEEP_MS / 1000.0

    for index in range(cursor, batch_end):
        item = queue[index]
        song_id = int(item["song_id"])
        try:
            evidence = client.fetch_song_evidence(song_id, st.session_state["cookie"])
        except Exception as exc:
            recovered, message = try_recover_local_api(client.base_url)
            if recovered:
                st.session_state["api_health_ok"] = True
                st.session_state["api_health_message"] = message
                try:
                    evidence = client.fetch_song_evidence(song_id, st.session_state["cookie"])
                except Exception as retry_exc:
                    st.session_state["extract_last_error"] = sanitize_error_message(retry_exc)
                    st.session_state["extract_running"] = False
                    st.session_state["extract_paused"] = True
                    refresh_extracted_df_from_state()
                    return
            else:
                st.session_state["api_health_ok"] = False
                st.session_state["api_health_message"] = message
                st.session_state["extract_last_error"] = f"{message} 原始错误: {sanitize_error_message(exc)}"
                st.session_state["extract_running"] = False
                st.session_state["extract_paused"] = True
                refresh_extracted_df_from_state()
                return

        raw_tags = [tag.strip() for tag in evidence.similar_playlist_tags if tag and tag.strip()]
        raw_tags = list(dict.fromkeys(raw_tags))
        rows.append(
            {
                "song_id": song_id,
                "song_name": item["song_name"],
                "artist": item["artist"],
                "wiki_style": evidence.wiki_style or "",
                "raw_tags": ", ".join(raw_tags),
                "final_genre": "",
                "final_subgenre": "",
                "language": "",
                "mood": "",
                "scene": "",
                "theme": "",
                "confidence": None,
                "decision_source": "",
                "reason": "",
                "needs_review": False,
                "review_note": "",
            }
        )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    st.session_state["extract_rows"] = rows
    st.session_state["extract_cursor"] = batch_end
    st.session_state["extract_finished"] = batch_end >= total
    if st.session_state["extract_finished"]:
        st.session_state["extract_running"] = False
        st.session_state["extract_paused"] = False
    refresh_extracted_df_from_state()


def render_sidebar(config: SpectraConfig) -> tuple[str, bool]:
    with st.sidebar:
        st.header("运行配置")
        base_url = st.text_input(
            "网易云 API Base URL",
            value=config.netease_api_base_url,
            key="sidebar_api_base_url",
        ).strip() or config.netease_api_base_url
        auto_save_login = st.checkbox(
            "登录后自动写回 .env",
            value=config.auto_save_login,
            key="sidebar_auto_save_login",
        )

        health_left, health_right = st.columns(2)
        if health_left.button("检查 API", key="sidebar_check_api", use_container_width=True):
            ok, message = check_api_health(base_url)
            st.session_state["api_health_ok"] = ok
            st.session_state["api_health_message"] = message
        if health_right.button("启动本地 API", key="sidebar_start_api", use_container_width=True):
            try:
                start_local_api_process(base_url)
                st.session_state["api_health_ok"] = None
                st.session_state["api_health_message"] = "已尝试启动本地 NeteaseCloudMusicApi。"
            except Exception as exc:
                st.session_state["api_health_ok"] = False
                st.session_state["api_health_message"] = sanitize_error_message(exc)

        if st.session_state["api_health_ok"] is True:
            st.success(f"API 正常: {st.session_state['api_health_message']}")
        elif st.session_state["api_health_ok"] is False:
            st.error(f"API 异常: {st.session_state['api_health_message']}")
        elif st.session_state["api_health_message"]:
            st.info(st.session_state["api_health_message"])

        if st.button("重置当前会话", key="sidebar_reset_session", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    return base_url, auto_save_login


def main() -> None:
    st.set_page_config(page_title="Spectra", layout="wide")
    load_dotenv_file(ENV_PATH)
    init_state()
    app_config = load_config()

    if not st.session_state["env_cookie_loaded"]:
        env_cookie = os.environ.get("NETEASE_COOKIE", "").strip()
        env_uid = os.environ.get("NETEASE_UID", "").strip()
        if env_cookie:
            st.session_state["cookie"] = env_cookie
        if env_uid:
            st.session_state["uid"] = env_uid
        st.session_state["env_cookie_loaded"] = True

    normalizer = GenreNormalizer(TAXONOMY_PATH, TAG_RULES_PATH)
    store = ProcessedStore(PROCESSED_PATH)
    base_url, auto_save_login = render_sidebar(app_config)
    client = get_client(base_url)
    if st.session_state["uid"]:
        client.set_uid(st.session_state["uid"])

    st.title("Spectra")
    st.caption("把网易云大歌单拆成更小、更可控的歌单。")

    st.subheader("1) 扫码鉴权")
    auth_left, auth_right = st.columns([0.65, 0.35])
    with auth_left:
        button_row = st.columns(2)
        if button_row[0].button("生成二维码", key="auth_generate_qr"):
            try:
                st.session_state["qr_payload"] = client.generate_qr_payload()
            except Exception as exc:
                st.error(sanitize_error_message(exc))
        if button_row[1].button("检查扫码状态", key="auth_check_qr"):
            qr_payload = st.session_state["qr_payload"]
            if not qr_payload:
                st.warning("请先生成二维码。")
            else:
                try:
                    result = client.authorize_with_qr(qr_payload["qr_key"])
                except Exception as exc:
                    st.error(sanitize_error_message(exc))
                else:
                    status = result.get("status")
                    message = str(result.get("message", ""))
                    if status == "success":
                        cookie = str(result.get("cookie", ""))
                        uid = str(result.get("uid", ""))
                        st.session_state["cookie"] = cookie
                        st.session_state["uid"] = uid
                        client.set_uid(uid)
                        if auto_save_login:
                            upsert_env_values(
                                ENV_PATH,
                                {
                                    "NETEASE_COOKIE": cookie,
                                    "NETEASE_UID": uid,
                                },
                                remove_keys=OBSOLETE_ENV_KEYS,
                            )
                        st.success(message)
                    elif status == "wait_scan":
                        st.info(message)
                    elif status == "wait_confirm":
                        st.warning(message)
                    elif status == "expired":
                        st.error(message)
                    else:
                        st.error(message or "扫码登录失败。")

        qr_payload = st.session_state["qr_payload"]
        if qr_payload:
            qr_bytes = decode_qr_data_uri(str(qr_payload.get("qr_img_data_uri", "")))
            if qr_bytes:
                st.image(qr_bytes, caption="使用网易云音乐 App 扫码", width=240)
            qr_url = str(qr_payload.get("qr_url", "")).strip()
            if qr_url:
                st.code(qr_url, language="text")
        if st.session_state["cookie"]:
            st.success("当前会话已持有网易云登录态。")
            if st.session_state["uid"]:
                st.caption(f"UID: {st.session_state['uid']}")
        else:
            st.info("还没有登录态。可以扫码获取，也可以在 .env 中放入 NETEASE_COOKIE。")

    with auth_right:
        st.markdown("**怎么用**")
        st.markdown("1. 生成二维码并扫码登录")
        st.markdown("2. 拉取歌单后读取目标歌单")
        st.markdown("3. 抓完标签后，到后面做人审和选歌")

    st.subheader("2) 歌单读取与增量比对")
    playlist_controls = st.columns([0.2, 0.2, 0.6])
    if playlist_controls[0].button("拉取我的歌单", key="load_playlists"):
        if not st.session_state["cookie"]:
            st.warning("请先完成扫码登录或配置 NETEASE_COOKIE。")
        else:
            try:
                rows = client.list_playlists(st.session_state["cookie"])
            except Exception as exc:
                st.error(sanitize_error_message(exc))
            else:
                playlists_df = pd.DataFrame(rows)
                st.session_state["playlists_df"] = playlists_df
                st.session_state["dist_playlists_df"] = playlists_df.copy()
                if not playlists_df.empty:
                    first_playlist_id = str(playlists_df.iloc[0]["playlist_id"])
                    if st.session_state["selected_playlist_id"] not in playlists_df["playlist_id"].astype(str).tolist():
                        st.session_state["selected_playlist_id"] = first_playlist_id
                st.success(f"已拉取 {len(playlists_df)} 个歌单。")

    playlists_df = st.session_state["playlists_df"]
    if playlists_df.empty:
        st.info("先拉取歌单列表，再读取目标歌单。")
    else:
        playlist_name_map = get_playlist_name_map(playlists_df)
        playlist_ids = playlists_df["playlist_id"].astype(str).tolist()
        if st.session_state["selected_playlist_id"] not in playlist_ids:
            st.session_state["selected_playlist_id"] = playlist_ids[0]
        selected_playlist_id = st.selectbox(
            "选择要处理的歌单",
            options=playlist_ids,
            format_func=lambda playlist_id: playlist_name_map.get(playlist_id, playlist_id),
            key="selected_playlist_id",
        )
        if playlist_controls[1].button("读取当前歌单", key="load_selected_playlist"):
            try:
                tracks = client.get_playlist_tracks(selected_playlist_id, st.session_state["cookie"])
            except Exception as exc:
                st.error(sanitize_error_message(exc))
            else:
                tracks_df = to_tracks_dataframe(tracks)
                processed_ids = store.list_processed_ids(normalizer.taxonomy_version)
                new_tracks_df = tracks_df[~tracks_df["song_id"].isin(processed_ids)].reset_index(drop=True)
                st.session_state["tracks_df"] = tracks_df
                st.session_state["new_tracks_df"] = new_tracks_df
                st.session_state["current_playlist_name"] = playlist_name_map.get(selected_playlist_id, selected_playlist_id)
                reset_playlist_workflow_state()
                st.success(f"已读取 {len(tracks_df)} 首歌曲。")

    tracks_df = st.session_state["tracks_df"]
    new_tracks_df = st.session_state["new_tracks_df"]
    if not tracks_df.empty:
        processed_count = len(tracks_df) - len(new_tracks_df)
        metrics = st.columns(4)
        metrics[0].metric("当前歌单", st.session_state["current_playlist_name"] or "未命名")
        metrics[1].metric("总歌曲数", len(tracks_df))
        metrics[2].metric("processed.json 已覆盖", processed_count)
        metrics[3].metric("当前新增歌曲", len(new_tracks_df))
        st.dataframe(tracks_df, use_container_width=True, hide_index=True)

    st.subheader("3) 原始标签挖掘")
    if tracks_df.empty:
        st.info("先读取一个歌单，再开始抓原始标签。")
    else:
        source_df = tracks_df.copy()
        current_signature = dataframe_signature(source_df)
        if st.session_state["extract_source_signature"] != current_signature:
            init_extract_task(source_df)

        extract_controls = st.columns(3)
        if extract_controls[0].button("开始 / 继续抓取", key="extract_start"):
            st.session_state["extract_running"] = True
            st.session_state["extract_paused"] = False
            st.session_state["extract_last_error"] = ""
        if extract_controls[1].button("暂停抓取", key="extract_pause"):
            st.session_state["extract_running"] = False
            st.session_state["extract_paused"] = True
        if extract_controls[2].button("从头重新抓取", key="extract_restart"):
            init_extract_task(source_df)
            st.session_state["extract_running"] = True
            st.session_state["extract_paused"] = False

        total = int(st.session_state["extract_total"])
        cursor = int(st.session_state["extract_cursor"])
        progress = 0.0 if total == 0 else min(cursor / total, 1.0)
        st.progress(progress, text=f"已抓取 {cursor} / {total} 首")
        if st.session_state["extract_paused"] and not st.session_state["extract_finished"]:
            st.warning("抓取已暂停。")
        if st.session_state["extract_finished"]:
            st.success("原始标签抓取完成。")
        if st.session_state["extract_last_error"]:
            st.error(st.session_state["extract_last_error"])

        extracted_df = st.session_state["extracted_df"]
        if extracted_df.empty:
            st.caption("抓取结果会显示在这里。")
        else:
            st.dataframe(extracted_df[["song_name", "artist", "wiki_style", "raw_tags"]], use_container_width=True, hide_index=True)

    st.subheader("4) 标签归一化清洗")
    extracted_df = coerce_review_dataframe(st.session_state["extracted_df"])
    if extracted_df.empty:
        st.info("先完成原始标签抓取，再执行归一化。")
    else:
        if st.button("执行归一化", key="run_normalization"):
            normalized_df = normalize_extracted_df(extracted_df, normalizer)
            st.session_state["normalized_df"] = normalized_df
            st.session_state["review_df"] = normalized_df.copy()
            st.session_state["single_playlist_selected_song_map"] = {}
            clear_single_playlist_selected_filters(reset_search=True)
            st.success("归一化完成。")

        normalized_df = coerce_review_dataframe(st.session_state["normalized_df"])
        if normalized_df.empty:
            st.caption("点击上面的按钮后，会在这里显示归一化摘要。")
        else:
            clean_df = build_tag_wall_source(normalized_df)
            summary_cols = st.columns(4)
            summary_cols[0].metric("归一化歌曲数", len(normalized_df))
            summary_cols[1].metric("待确认", int(normalized_df["needs_review"].sum()))
            summary_cols[2].metric("可直接选歌", len(clean_df))
            summary_cols[3].metric("主类目数", normalized_df["final_genre"].nunique())

            genre_summary = (
                normalized_df["final_genre"]
                .fillna("")
                .replace("", "Unknown")
                .value_counts()
                .rename_axis("final_genre")
                .reset_index(name="count")
            )
            st.dataframe(genre_summary, use_container_width=True, hide_index=True)

    st.subheader("5) 可选：人工微调")
    review_df = coerce_review_dataframe(st.session_state["review_df"])
    if review_df.empty:
        st.info("先执行归一化。通常你可以直接去下一步选歌。")
    else:
        language_options = list(normalizer.dimension_alias_map.get("language", {}).keys()) + ["Unknown"]
        mood_options = list(normalizer.dimension_alias_map.get("mood", {}).keys()) + ["Unknown"]
        scene_options = list(normalizer.dimension_alias_map.get("scene", {}).keys()) + ["Unknown"]
        theme_options = list(normalizer.dimension_alias_map.get("theme", {}).keys()) + ["Unknown"]
        genre_options = list(normalizer.genres) + ["Other"]

        review_summary = st.columns([0.22, 0.22, 0.22, 0.34])
        needs_review_count = int(review_df["needs_review"].sum())
        review_summary[0].metric("总结果", len(review_df))
        review_summary[1].metric("待确认", needs_review_count)
        review_summary[2].metric("可直接选歌", len(review_df[review_df["final_genre"].fillna("") != "Other"]))
        review_summary[3].markdown(
            "**这一步可以跳过**\n\n"
            "如果分类看起来差不多，直接去下一步选歌。\n"
            "只有当分类明显不对时，再展开下面这张表修改。"
        )

        with st.expander("打开人工微调表", expanded=False):
            st.caption("这里的修改会自动用于下一步选歌。")
            edited_review_df = st.data_editor(
                review_df[
                    [
                        "song_id",
                        "song_name",
                        "artist",
                        "raw_tags",
                        "final_genre",
                        "final_subgenre",
                        "language",
                        "mood",
                        "scene",
                        "theme",
                        "needs_review",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
                key="review_editor",
                column_config={
                    "song_id": st.column_config.TextColumn("song_id", disabled=True),
                    "song_name": st.column_config.TextColumn("歌曲", disabled=True),
                    "artist": st.column_config.TextColumn("歌手", disabled=True),
                    "raw_tags": st.column_config.TextColumn("原始标签", disabled=True, width="large"),
                    "final_genre": st.column_config.SelectboxColumn("一级类目", options=genre_options),
                    "final_subgenre": st.column_config.TextColumn("二级类目"),
                    "language": st.column_config.SelectboxColumn("语种", options=language_options),
                    "mood": st.column_config.SelectboxColumn("情绪", options=mood_options),
                    "scene": st.column_config.SelectboxColumn("场景", options=scene_options),
                    "theme": st.column_config.SelectboxColumn("主题", options=theme_options),
                    "needs_review": st.column_config.CheckboxColumn("暂不参与选歌"),
                },
                disabled=["song_id", "song_name", "artist", "raw_tags"],
            )
            edited_review_df = edited_review_df.copy()
            edited_review_df["wiki_style"] = review_df["wiki_style"].values
            edited_review_df["confidence"] = review_df["confidence"].values
            edited_review_df["decision_source"] = review_df["decision_source"].values
            edited_review_df["reason"] = review_df["reason"].values
            edited_review_df["review_note"] = review_df["review_note"].values
            st.session_state["review_df"] = coerce_review_dataframe(edited_review_df[REVIEW_COLUMNS])

    st.subheader("6) 单歌单标签分发")
    review_df = coerce_review_dataframe(st.session_state["review_df"])
    tag_wall_source_df = build_tag_wall_source(review_df)
    if review_df.empty:
        st.info("先完成归一化，再使用标签墙选歌。")
    else:
        st.caption("同一块里可以多选；不同块会一起生效。")
        if tag_wall_source_df.empty:
            st.warning("当前还没有可选歌曲。")
        else:
            ensure_single_playlist_filter_state(tag_wall_source_df)
            st.text_input(
                "搜索标签",
                key="single_playlist_tag_search",
                placeholder="输入标签关键词，快速找到想点的标签。",
            )
            render_selected_filter_box()

            raw_tag_left, raw_tag_right = st.columns([0.8, 0.2])
            toggle_label = "展开全部原始标签" if not st.session_state["single_playlist_raw_tags_expanded"] else "收起原始标签"
            if raw_tag_right.button(toggle_label, key="toggle_raw_tag_view", use_container_width=True):
                st.session_state["single_playlist_raw_tags_expanded"] = not st.session_state["single_playlist_raw_tags_expanded"]
                st.rerun()

            tag_options = build_single_playlist_tag_options(
                tag_wall_source_df,
                st.session_state["single_playlist_tag_search"],
            )

            with raw_tag_left:
                render_single_playlist_tag_group("原始标签", "raw_tags", tag_options["raw_tags"])
            for column, label in SINGLE_PLAYLIST_GROUPS[1:]:
                render_single_playlist_tag_group(label, column, tag_options[column])

            if len(st.session_state[single_playlist_filter_key("raw_tags")]) >= 3:
                st.caption("已选 3 个及以上原始标签，系统会自动收紧匹配；结果出来后仍可在表格里手动取消歌曲。")

            filtered_song_df = apply_single_playlist_tag_filters(tag_wall_source_df)
            if selected_filter_count() == 0:
                st.info("先点标签，再查看命中歌曲。")
            elif filtered_song_df.empty:
                st.warning("当前标签组合命中 0 首歌曲。标签会保留，你可以手动退条件。")
            else:
                st.metric("命中歌曲数", len(filtered_song_df))
                st.text_input(
                    "搜索结果区歌曲 / 歌手",
                    key="single_playlist_song_search",
                    placeholder="输入歌名或歌手名，缩小当前结果。",
                )
                song_query = st.session_state["single_playlist_song_search"].strip().lower()
                if song_query:
                    filtered_song_df = filtered_song_df[
                        filtered_song_df.apply(
                            lambda row: song_query in str(row["song_name"]).lower()
                            or song_query in str(row["artist"]).lower(),
                            axis=1,
                        )
                    ].reset_index(drop=True)

                if filtered_song_df.empty:
                    st.warning("结果区搜索后没有命中歌曲。")
                else:
                    display_song_df = sync_selected_song_map(filtered_song_df)
                    editor_df = st.data_editor(
                        display_song_df[
                            [
                                "selected",
                                "song_id",
                                "song_name",
                                "artist",
                                "final_genre",
                                "final_subgenre",
                                "language",
                                "mood",
                                "scene",
                                "theme",
                            ]
                        ],
                        use_container_width=True,
                        hide_index=True,
                        key="single_playlist_result_editor",
                        column_config={
                            "selected": st.column_config.CheckboxColumn("选择", default=True),
                            "song_id": st.column_config.TextColumn("song_id", disabled=True),
                            "song_name": st.column_config.TextColumn("歌曲", disabled=True),
                            "artist": st.column_config.TextColumn("歌手", disabled=True),
                            "final_genre": st.column_config.TextColumn("一级类目", disabled=True),
                            "final_subgenre": st.column_config.TextColumn("二级类目", disabled=True),
                            "language": st.column_config.TextColumn("语种", disabled=True),
                            "mood": st.column_config.TextColumn("情绪", disabled=True),
                            "scene": st.column_config.TextColumn("场景", disabled=True),
                            "theme": st.column_config.TextColumn("主题", disabled=True),
                        },
                        disabled=["song_id", "song_name", "artist", "final_genre", "final_subgenre", "language", "mood", "scene", "theme"],
                    )
                    save_selected_song_map(editor_df)
                    selected_rows_df = editor_df[editor_df["selected"]].copy().reset_index(drop=True)
                    st.caption(f"默认全选命中歌曲；当前保留 {len(selected_rows_df)} 首。")

                    dist_df = st.session_state["dist_playlists_df"]
                    dist_name_map = get_playlist_name_map(dist_df)
                    action_mode = st.radio(
                        "写入方式",
                        options=["加入已有歌单", "新建歌单"],
                        horizontal=True,
                        key="single_playlist_action_mode",
                    )
                    action_top = st.columns([0.2, 0.8])
                    if action_top[0].button("刷新目标歌单", key="refresh_dist_playlists", use_container_width=True):
                        try:
                            rows = client.list_playlists(st.session_state["cookie"])
                        except Exception as exc:
                            st.error(sanitize_error_message(exc))
                        else:
                            st.session_state["dist_playlists_df"] = pd.DataFrame(rows)
                            dist_df = st.session_state["dist_playlists_df"]
                            dist_name_map = get_playlist_name_map(dist_df)
                            st.success("目标歌单列表已刷新。")

                    target_playlist_id = ""
                    new_playlist_name = ""
                    if action_mode == "加入已有歌单":
                        if dist_df.empty:
                            st.info("先刷新目标歌单列表。")
                        else:
                            options = dist_df["playlist_id"].astype(str).tolist()
                            target_playlist_id = st.selectbox(
                                "选择目标歌单",
                                options=options,
                                format_func=lambda playlist_id: dist_name_map.get(playlist_id, playlist_id),
                                key="single_playlist_target_playlist_id",
                            )
                    else:
                        new_playlist_name = st.text_input(
                            "新歌单名称",
                            key="single_playlist_new_name",
                            placeholder="例如：深夜华语流行 / 夏天开车用 / ACG 安静向",
                        ).strip()

                    write_processed = st.checkbox(
                        "同步写入 processed.json 记录",
                        value=True,
                        key="single_playlist_write_processed",
                    )

                    if st.button("创建 / 加入（单歌单）", key="submit_single_playlist_distribution"):
                        if not st.session_state["cookie"]:
                            st.warning("请先完成登录。")
                        elif selected_rows_df.empty:
                            st.warning("当前没有选中任何歌曲。")
                        else:
                            try:
                                selected_song_ids = [int(value) for value in selected_rows_df["song_id"].tolist()]
                                added_song_ids = list(selected_song_ids)
                                target_label = ""
                                if action_mode == "加入已有歌单":
                                    if not target_playlist_id:
                                        st.warning("请先选择目标歌单。")
                                        st.stop()
                                    existing_tracks = client.get_playlist_tracks(target_playlist_id, st.session_state["cookie"])
                                    existing_song_ids = {track.song_id for track in existing_tracks}
                                    added_song_ids = [song_id for song_id in selected_song_ids if song_id not in existing_song_ids]
                                    if added_song_ids:
                                        client.add_tracks_to_playlist(target_playlist_id, added_song_ids, st.session_state["cookie"])
                                    target_label = dist_name_map.get(target_playlist_id, target_playlist_id)
                                else:
                                    if not new_playlist_name:
                                        st.warning("请先填写新歌单名称。")
                                        st.stop()
                                    created_playlist_id = client.create_playlist(new_playlist_name, st.session_state["cookie"])
                                    client.add_tracks_to_playlist(created_playlist_id, selected_song_ids, st.session_state["cookie"])
                                    target_label = new_playlist_name

                                if write_processed:
                                    records = []
                                    for row in selected_rows_df.to_dict("records"):
                                        full_row = review_df[review_df["song_id"].astype(int) == int(row["song_id"])].iloc[0]
                                        records.append(
                                            {
                                                "song_id": int(full_row["song_id"]),
                                                "song_name": full_row["song_name"],
                                                "artist": full_row["artist"],
                                                "final_genre": full_row["final_genre"],
                                                "final_subgenre": full_row["final_subgenre"],
                                                "language": full_row["language"],
                                                "mood": full_row["mood"],
                                                "scene": full_row["scene"],
                                                "theme": full_row["theme"],
                                                "taxonomy_version": normalizer.taxonomy_version,
                                            }
                                        )
                                    store.upsert(records)

                                if action_mode == "加入已有歌单" and not added_song_ids:
                                    st.info(f"目标歌单 {target_label} 已经包含这些歌曲，没有新增。")
                                else:
                                    count = len(added_song_ids) if action_mode == "加入已有歌单" else len(selected_song_ids)
                                    st.success(f"已向 {target_label} 写入 {count} 首歌曲。")
                            except Exception as exc:
                                st.error(sanitize_error_message(exc))

    if st.session_state["extract_running"] and not st.session_state["extract_paused"]:
        process_extract_batch(client)
        st.rerun()


if __name__ == "__main__":
    main()
