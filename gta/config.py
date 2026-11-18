"""Load and save gta/config.json and resolve the local state directory."""
import json
import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PKG_DIR / "config.json"


def load_config(path=CONFIG_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg, path=CONFIG_PATH):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def state_path(name):
    """Local state lives in gta/state/ (git-ignored). Override with GTA_STATE_DIR."""
    root = Path(os.environ.get("GTA_STATE_DIR", PKG_DIR / "state"))
    root.mkdir(parents=True, exist_ok=True)
    return root / name
