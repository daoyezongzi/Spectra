from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config.toml"
DEFAULT_NETEASE_API_PORT = 18631
DEFAULT_NETEASE_API_BASE_URL = f"http://127.0.0.1:{DEFAULT_NETEASE_API_PORT}"
DEFAULT_AUTO_SAVE_LOGIN = True


@dataclass(frozen=True)
class SpectraConfig:
    netease_api_base_url: str = DEFAULT_NETEASE_API_BASE_URL
    auto_save_login: bool = DEFAULT_AUTO_SAVE_LOGIN


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def load_config(config_path: Path = CONFIG_PATH) -> SpectraConfig:
    if not config_path.exists():
        return SpectraConfig()

    with config_path.open("rb") as file:
        raw = tomllib.load(file)

    spectra = raw.get("spectra", {})
    base_url = str(spectra.get("netease_api_base_url", DEFAULT_NETEASE_API_BASE_URL)).strip()
    if not base_url:
        base_url = DEFAULT_NETEASE_API_BASE_URL

    auto_save_login = _coerce_bool(
        spectra.get("auto_save_login", DEFAULT_AUTO_SAVE_LOGIN),
        DEFAULT_AUTO_SAVE_LOGIN,
    )
    return SpectraConfig(
        netease_api_base_url=base_url,
        auto_save_login=auto_save_login,
    )
