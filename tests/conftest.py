"""Pytest bootstrap.

Point ZSHEET_DATA_DIR at a throwaway temp dir BEFORE any backend import, so
engine/session construction never touches the developer's local ./data.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("ZSHEET_DATA_DIR", tempfile.mkdtemp(prefix="zsheet-test-"))
