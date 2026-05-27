"""Session-wide test fixtures.

Provides a defensive cleanup hook: after the entire test session finishes,
any book whose title begins with `TEST_` (or `TEST `) is deleted, along
with its associated asset records and revisions. This guards against
individual tests forgetting to tear down their own fixtures so the user's
library page never accumulates orphaned test data.
"""
from __future__ import annotations

import os
import pytest
import requests


def _api_base() -> str:
    base = os.environ.get("REACT_APP_BACKEND_URL")
    if not base:
        base = "https://manuscript-app-2.preview.emergentagent.com"
    return f"{base.rstrip('/')}/api"


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_books_after_session():
    yield
    # Runs once after every test in the session has completed.
    api = _api_base()
    try:
        resp = requests.get(f"{api}/books", timeout=15)
        if resp.status_code != 200:
            return
        for book in resp.json():
            title = (book.get("title") or "").strip()
            # Match both `TEST_` (snake-case) and `TEST ` (loose) prefixes,
            # plus any "(copy)" duplicates the suite produces.
            if title.startswith("TEST_") or title.startswith("TEST "):
                try:
                    requests.delete(f"{api}/books/{book['id']}", timeout=10)
                except Exception:
                    pass
    except Exception:
        # Never fail the test session because of cleanup hiccups.
        pass
