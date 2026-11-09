"""
Translations sent with parse_mode=MarkdownV2 must be valid, otherwise Telegram
rejects the whole message ("can't parse entities") and the flow breaks.
Both languages must define the same keys.
"""
import json
import re
from pathlib import Path

import pytest

from tests.markdown_v2 import markdown_v2_errors

LOCALES = Path(__file__).parent.parent / "i18n" / "locales"

# Sections/keys used as plain text (button labels, popups, captions)
PLAIN_SECTIONS = {"buttons", "toasts", "list"}
PLAIN_KEYS = {
    "commands.custom_not_found", "commands.file_caption",
    "metadata.sending_file", "metadata.file_too_large", "metadata.exceeds_limit",
    "metadata.send_error", "metadata.file_sent",
}


def _flatten(data, prefix=""):
    for key, value in data.items():
        if isinstance(value, dict):
            yield from _flatten(value, f"{prefix}{key}.")
        else:
            yield f"{prefix}{key}", value


def _load(lang):
    with open(LOCALES / f"{lang}.json", encoding="utf-8") as f:
        return dict(_flatten(json.load(f)))


@pytest.mark.parametrize("lang", ["en", "it"])
def test_markdown_v2_strings_are_valid(lang):
    broken = {}
    for key, value in _load(lang).items():
        if key.split(".")[0] in PLAIN_SECTIONS or key in PLAIN_KEYS:
            continue
        # placeholders are always filled with already-escaped values
        errors = markdown_v2_errors(re.sub(r"\{[a-z_]+\}", "X", value))
        if errors:
            broken[key] = errors
    assert broken == {}


def test_languages_have_the_same_keys():
    assert set(_load("en")) == set(_load("it"))
