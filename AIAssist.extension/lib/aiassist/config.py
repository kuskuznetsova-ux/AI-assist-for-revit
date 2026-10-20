# -*- coding: utf-8 -*-
"""Settings: ~/.aiassist/config.json, env vars override the file."""
import os
import io
import json

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".aiassist")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
RECIPES_PATH = os.path.join(CONFIG_DIR, "recipes.md")

DEFAULT_MODEL = "claude-sonnet-5-5"


def _read_file():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with io.open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load():
    data = _read_file()
    return {
        "api_key": os.environ.get("ANTHROPIC_API_KEY") or data.get("api_key", ""),
        "model": os.environ.get("AIASSIST_MODEL") or data.get("model", DEFAULT_MODEL),
        "max_tokens": int(data.get("max_tokens", 4096)),
    }


def load_recipes():
    """User-taught rules, appended to the system prompt."""
    if not os.path.exists(RECIPES_PATH):
        return u""
    try:
        with io.open(RECIPES_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return u""


def append_recipe(text):
    if not os.path.isdir(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)
    with io.open(RECIPES_PATH, "a", encoding="utf-8") as f:
        f.write(u"- " + text.strip() + u"\n")
