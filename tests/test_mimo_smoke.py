"""
Smoke test: verify Mimo API is reachable through system proxy.
This test catches the ProxyHandler({}) / max_tokens regressions
that unit tests (with mocked urllib) cannot.

Run: python -m pytest tests/test_mimo_smoke.py -v --run-smoke
Skip by default (requires network + API key).
"""

import json
import os
import pathlib
import urllib.request
import pytest


def _get_mimo_key():
    auth_path = pathlib.Path.home() / ".openclaw/agents/leader/agent/auth-profiles.json"
    if not auth_path.exists():
        return None
    with open(auth_path) as f:
        auth = json.load(f)
    return auth.get("profiles", {}).get("xiaomimimo:default", {}).get("key")


MIMO_KEY = _get_mimo_key()
SKIP_REASON = "Set --run-smoke to run live Mimo connectivity tests"


def pytest_addoption(parser):
    parser.addoption("--run-smoke", action="store_true", default=False,
                    help="Run live connectivity smoke tests")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-smoke"):
        skip = pytest.mark.skip(reason="Set --run-smoke to run live Mimo tests")
        for item in items:
            if "Smoke" in item.keywords or "test_mimo_smoke" in str(item.fspath):
                item.add_marker(skip)


@pytest.mark.skipif(MIMO_KEY is None, reason="No Mimo API key found")
class TestMimoConnectivity:
    """Live connectivity tests — NOT mocked, requires network."""

    BASE_URL = "https://token-plan-sgp.xiaomimimo.com/v1"
    MODEL = "mimo-v2.5-pro"

    def test_simple_request_via_system_proxy(self):
        """Basic request through system proxy should succeed."""
        payload = {
            "model": self.MODEL,
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 50,
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.BASE_URL}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {MIMO_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
        assert "choices" in body
        assert body["choices"][0]["message"]["content"]

    def test_proxyhandler_bypass_fails(self):
        """ProxyHandler({}) should FAIL for Mimo (must use system proxy)."""
        payload = {
            "model": self.MODEL,
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 50,
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.BASE_URL}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {MIMO_KEY}",
            },
        )
        # This MUST fail — Mimo requires system proxy
        no_proxy_opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )
        with pytest.raises((ConnectionError, OSError, Exception)):
            with no_proxy_opener.open(req, timeout=15) as resp:
                resp.read()

    def test_max_tokens_16384_works(self):
        """max_tokens=16384 should work (current production value)."""
        payload = {
            "model": self.MODEL,
            "messages": [{"role": "user", "content": "List 5 colors"}],
            "max_tokens": 16384,
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.BASE_URL}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {MIMO_KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
        assert "choices" in body
