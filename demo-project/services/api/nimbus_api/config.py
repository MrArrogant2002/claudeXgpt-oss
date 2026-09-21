"""Central configuration for the Nimbus API service.

Values come from environment variables with sensible local defaults, so the
service runs offline with zero setup. See ../../.env.example for the full list.
"""

import os

# base62 alphabet — encode() and decode() must agree on this exact ordering.
ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE = len(ALPHABET)  # 62

# ":memory:" keeps everything in-process (default). Point NIMBUS_DB at a file path
# to persist across restarts.
DB_PATH = os.environ.get("NIMBUS_DB", ":memory:")

MAX_URL_LENGTH = int(os.environ.get("NIMBUS_MAX_URL_LENGTH", "2048"))
ALLOWED_SCHEMES = ("http", "https")
