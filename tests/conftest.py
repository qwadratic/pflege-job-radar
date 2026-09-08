"""Shared test defaults. AUTH_DISABLED=1: the identity middleware (app/auth.py) does not gate owner-only routes, so the
existing API tests keep calling POST /api/crawl etc. without exe.dev headers. tests/test_auth.py switches it off per test."""
import os

os.environ.setdefault("AUTH_DISABLED", "1")
