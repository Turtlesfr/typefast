"""Settings, stored as JSON under ~/.config/typefast/."""

import copy
import json
import os
import tempfile
from pathlib import Path

from typefast import languages

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "typefast"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "typefast"
CONFIG_FILE = CONFIG_DIR / "config.json"
VOCABULARY_FILE = CONFIG_DIR / "vocabulary.json"
HISTORY_FILE = DATA_DIR / "history.json"

TRIGGER_KEYS = ("ctrl", "shift", "alt")

DEFAULTS = {
    "api_key": "",
    # "us" or "eu": which AssemblyAI data zone handles the audio.
    "region": "us",
    # Languages offered in the overlay, in display order.
    "languages": ["en", "fr", "es"],
    "last_language": "en",
    # "auto" (word by word where possible) or "universal-3-6-pro".
    "live_model": "auto",
    # Style the overlay opens with: "plain", "clean" or "formatted".
    "default_style": "clean",
    # Rewrite instruction for the Formatted style; empty uses the built-in one.
    "formatted_instruction": "",
    # When the Dictation API is late, rewrite the live text with a fast model instead.
    "backup": True,
    "backup_model": "gemini-2.5-flash-lite",
    "strip_inverted_punctuation": False,
    "trigger": {"key": "ctrl", "taps": 3, "window_ms": 1000},
    # Milliseconds between injected keystrokes. Some apps drop characters at 0.
    "typing_delay_ms": 4,
    # Final replacements longer than this many characters are pasted, not typed.
    "paste_threshold": 300,
    # sounddevice input device name, or null for the system default.
    "microphone": None,
    # Where the live bar sits while dictating: "top" or "bottom".
    "hud_position": "top",
    # Longest wait for the polished text after you stop; then the live text stays.
    "polish_timeout_s": 10,
}


def _merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def write_json(path: Path, data, private: bool = False) -> None:
    """Write atomically, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        if private:
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError):
        # Keep the broken file for inspection instead of silently overwriting it.
        try:
            path.rename(path.with_suffix(path.suffix + ".broken"))
        except OSError:
            pass
        return default


def normalize(cfg: dict) -> dict:
    """Repair values a user may have mistyped in the JSON file."""
    raw = cfg.get("languages")
    langs = [c for c in raw if c in languages.BY_CODE] if isinstance(raw, list) else []
    cfg["languages"] = list(dict.fromkeys(langs)) or ["en"]
    if cfg.get("last_language") not in cfg["languages"]:
        cfg["last_language"] = cfg["languages"][0]
    if cfg.get("region") not in ("us", "eu"):
        cfg["region"] = "us"
    if cfg.get("default_style") not in ("plain", "clean", "formatted"):
        cfg["default_style"] = "clean"
    cfg.pop("final_text", None)  # replaced by default_style
    if not isinstance(cfg.get("formatted_instruction"), str):
        cfg["formatted_instruction"] = ""
    cfg["backup"] = bool(cfg.get("backup", True))
    if cfg.get("hud_position") not in ("top", "bottom"):
        cfg["hud_position"] = "top"
    if cfg.get("live_model") not in ("auto", languages.MODEL_U3_PRO):
        cfg["live_model"] = "auto"
    trig = cfg.get("trigger")
    if not isinstance(trig, dict):
        trig = cfg["trigger"] = dict(DEFAULTS["trigger"])
    if trig.get("key") not in TRIGGER_KEYS:
        trig["key"] = "ctrl"
    trig["taps"] = min(5, max(2, _int(trig.get("taps"), 3)))
    trig["window_ms"] = min(3000, max(400, _int(trig.get("window_ms"), 1000)))
    cfg["typing_delay_ms"] = min(50, max(0, _int(cfg.get("typing_delay_ms"), 4)))
    cfg["paste_threshold"] = max(20, _int(cfg.get("paste_threshold"), 300))
    cfg["polish_timeout_s"] = min(60, max(3, _int(cfg.get("polish_timeout_s"), 10)))
    if not isinstance(cfg.get("api_key"), str):
        cfg["api_key"] = ""
    return cfg


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load() -> dict:
    return normalize(_merge(DEFAULTS, read_json(CONFIG_FILE, {})))


def save(cfg: dict) -> None:
    write_json(CONFIG_FILE, cfg, private=True)
