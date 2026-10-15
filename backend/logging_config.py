"""Central logging setup for the Z-Sheet backend.

The codebase logs exclusively under the ``zsheet`` logger hierarchy (e.g.
``zsheet.llm``, ``zsheet.ingestion``). This module installs one console
handler for that hierarchy — no third-party loggers (uvicorn, sqlalchemy)
are reconfigured — and is idempotent so importing the app under uvicorn's
reloader or pytest does not stack duplicate handlers.

Set ``ZSHEET_LOG_LEVEL`` (DEBUG/INFO/WARNING/ERROR) to change verbosity.
Log messages must never contain the LLM API key or full spreadsheets:
provider failures are logged as exception text, and keys are only ever
rendered through :func:`backend.ai.provider.mask_key`.
"""

from __future__ import annotations

import logging
import sys

ROOT_LOGGER_NAME = "zsheet"

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Attach one stderr handler to the ``zsheet`` logger tree.

    Safe to call repeatedly (uvicorn reload, tests, create_app): only the
    first call installs the handler; later calls just adjust the level.
    """
    global _CONFIGURED

    root = logging.getLogger(ROOT_LOGGER_NAME)
    resolved = _resolve_level(level)
    root.setLevel(resolved)
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    # Keep propagation enabled: uvicorn/gunicorn do not configure the Python
    # root logger by default, so our handler remains the only output in
    # production, while pytest's caplog (which listens on root) still sees
    # every record.
    _CONFIGURED = True


def _resolve_level(level: str) -> int:
    resolved = logging.getLevelName(level.upper())
    return resolved if isinstance(resolved, int) else logging.INFO


def get_logger(name: str) -> logging.Logger:
    """Return a child logger of the ``zsheet`` hierarchy.

    Bare module names are accepted (``get_logger(__name__)`` from inside the
    ``backend`` package becomes ``zsheet.<module path>``); names already
    prefixed with ``zsheet`` are used as given.
    """
    if name == ROOT_LOGGER_NAME or name.startswith(ROOT_LOGGER_NAME + "."):
        return logging.getLogger(name)
    if name.startswith("backend."):
        name = name[len("backend.") :]
    elif name == "backend":
        name = ""
    return logging.getLogger(
        f"{ROOT_LOGGER_NAME}.{name}" if name else ROOT_LOGGER_NAME
    )
