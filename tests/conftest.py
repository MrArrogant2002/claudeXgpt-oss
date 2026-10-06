"""Shared fixtures. The Harmony encoding is loaded once per session from the
vendored vocabulary; tests needing it skip cleanly when it is unavailable."""

import os
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def enc():
    os.environ.setdefault(
        "TIKTOKEN_RS_CACHE_DIR", str(REPO / "vendor" / "tiktoken")
    )
    try:
        from openai_harmony import HarmonyEncodingName, load_harmony_encoding
    except ImportError:  # pragma: no cover
        pytest.skip("openai-harmony not installed")
    try:
        return load_harmony_encoding(HarmonyEncodingName.HARMONY_GPT_OSS)
    except Exception as e:  # pragma: no cover
        pytest.skip(f"harmony vocab unavailable: {e}")


@pytest.fixture
def E(enc):
    """Encode text including Harmony special tokens."""
    return lambda text: enc.encode(text, allowed_special="all")
