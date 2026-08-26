"""Shared test setup.

Loads `.env` before any test module is imported, so the live-API tests'
`skipif(not os.environ.get("ANTHROPIC_API_KEY"))` decorators — evaluated
at collection time — see the keys instead of skipping silently.
"""

from agentic.env import load_env

load_env()
