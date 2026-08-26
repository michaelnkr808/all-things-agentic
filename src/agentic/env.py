"""Load `.env` at process start so API keys reach the SDKs.

The Anthropic and google-genai clients both read their keys from the
environment at construction time. Nothing was calling this, so a `.env`
sitting in the repo root was invisible: live tests skipped themselves and
the CLI would have failed on the first model call.

Explicit by design — called from the three entry points (CLI, server,
pytest conftest) rather than at import time, so importing `agentic`
never mutates a caller's environment behind their back.

Real environment variables always win: `override=False` means
`ANTHROPIC_API_KEY=... python -m agentic.pipeline` still works, and CI
secrets are never clobbered by a stray checked-out file.
"""

from __future__ import annotations

import os
from pathlib import Path

# src/agentic/env.py -> src/agentic -> src -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]

_loaded = False


def load_env(path: str | os.PathLike[str] | None = None) -> bool:
    """Load the repo `.env` into os.environ. Returns True if a file was read.

    Idempotent: repeated calls (pytest importing several entry points) do
    nothing after the first. Missing python-dotenv or a missing file are
    both fine — the process simply keeps whatever the shell exported.
    """
    global _loaded
    if _loaded and path is None:
        return False

    try:
        from dotenv import load_dotenv
    except ImportError:  # optional dependency; shell-exported vars still work
        _loaded = True
        return False

    candidates = [Path(path)] if path is not None else [
        Path.cwd() / ".env",  # running from the repo root, the documented way
        _REPO_ROOT / ".env",  # running from anywhere else
    ]
    for candidate in candidates:
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            _loaded = True
            return True

    _loaded = True
    return False


__all__ = ["load_env"]
