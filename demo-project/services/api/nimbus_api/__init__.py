"""Nimbus API — a minimal, dependency-free URL shortener service.

The public surface is the `Api` request router and the `LinkStore` persistence
layer. Everything is stdlib-only so the service runs offline with no install.
"""

__version__ = "0.4.2"

from .app import Api
from .store import LinkStore, LinkError

__all__ = ["Api", "LinkStore", "LinkError", "__version__"]
