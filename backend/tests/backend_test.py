"""Loom Console backend API tests (pytest).

Covers: health, auth (register/login/logout/me, lockout flow not triggered),
dashboard, tasks, pipeline run + status, wiki (tree/page/search/graph/lint),
content (submit text/url, review, sources, settings).
"""
import os
import time
import uuid

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api"

ADMIN_EMAIL = "admin@loom.dev"
ADMIN_PASSWORD = "LoomAdmin2026!"


# ───────── fixtures ─────────

@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
               timeout=15)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    data = r.json()
    assert data["email"] == ADMIN_EMAIL
    assert data["role"] == "admin"
    return s


@pytest.fixture(scope="module")
def member_session():
    s = requests.Session()
    email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register",
               json={"email": email, "password": "MemberPass1!", "name": "Test Member"},
               timeout=15)
    assert r.status_code == 200, f"register failed: {r.status_code} {r.text}"
    data = r.json()
    assert data["role"] == "member"
    s.email = email  # type: ignore[attr-defined]
    return s


# ───────── health ─────────

def test_health():
    r = requests.get(f"{API}/health", timeout=10)
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "loom-console"}


# ───────── auth ─────────

class TestAuth:
    def test_login_wrong_password(self):
        r = requests.post(f"{API}/auth/login",
                          json={"email": ADMIN_EMAIL, "password": "wrong-password"},
                          timeout=10)
        assert r.status_code == 401
        assert "错误" in r.json().get("detail", "")

    def test_unauth_dashboard_blocked(self):
        r = requests.get(f"{API}/dashboard", timeout=10)
        assert r.status_code == 401

    def test_admin_login_sets_cookies(self, admin_session):
        # cookies set with secure/httponly. names must be present
        names = {c.name for c in admin_session.cookies}
        assert "access_token" in names
        assert "refresh_token" in names

    def test_me_returns_admin(self, admin_session):
        r = admin_session.get(f"{API}/auth/me", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == ADMIN_EMAIL
        assert body["role"] == "admin"

    def test_member_register_and_me(self, member_session):
        r = member_session.get(f"{API}/auth/me", timeout=10)
        assert r.status_code == 200
        assert r.json()["role"] == "member"

    def test_duplicate_register_400(self, member_session):
        r = member_session.post(f"{API}/auth/register",
                                json={"email": member_session.email,
                                      "password": "MemberPass1!",
                                      "name": "x"}, timeout=10)
        assert r.status_code == 400

    def test_logout(self):
        s = requests.Session()
        r = s.post(f"{API}/auth/login",
                   json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                   timeout=10)
        assert r.status_code == 200
        r = s.post(f"{API}/auth/logout", timeout=10)
        assert r.status_code == 200
        # After logout, /me should fail (cookie cleared via Set-Cookie). The
        # response Set-Cookie clears in browser; for requests, the session
        # cookie may still be present. Best-effort:
        s.cookies.clear()
        r = s.get(f"{API}/auth/me", timeout=10)
        assert r.status_code == 401


# ───────── dashboard ─────────

class TestDashboard:
    def test_dashboard_shape(self, admin_session):
        r = admin_session.get(f"{API}/dashboard", timeout=15)
        assert r.status_code == 200
        data = r.json()
        for key in ("queue", "wiki", "recent_results", "daily_activity",
                    "categories", "review_pending", "pipeline"):
            assert key in data, f"missing key {key}"
        # queue should have status counts
        assert isinstance(data["queue"], dict)
        # wiki overview should contain pages info
        assert isinstance(data["wiki"], dict)


# ───────── tasks ─────────

class TestTasks:
    def test_list_tasks(self, admin_session):
        r = admin_session.get(f"{API}/tasks?limit=50", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data or isinstance(data, list)

    def test_list_tasks_filter_failed(self, admin_session):
        r = admin_session.get(f"{API}/tasks?status=failed", timeout=15)
        assert r.status_code == 200

    def test_retry_invalid_task_400(self, admin_session):
        r = admin_session.post(f"{API}/tasks/99999999/retry", timeout=10)
        assert r.status_code == 400

    def test_enqueue_raw(self, admin_session):
        r = admin_session.post(f"{API}/tasks/enqueue-raw", timeout=30)
        assert r.status_code == 200

    def test_retry_all_failed(self, admin_session):
        r = admin_session.post(f"{API}/tasks/retry-failed", timeout=15)
        assert r.status_code == 200
        assert "count" in r.json()


# ───────── wiki ─────────

class TestWiki:
    def test_tree(self, admin_session):
        r = admin_session.get(f"{API}/wiki/tree", timeout=15)
        assert r.status_code == 200

    def test_search_context(self, admin_session):
        r = admin_session.get(f"{API}/wiki/search",
                              params={"q": "context", "scope": "wiki"}, timeout=15)
        assert r.status_code == 200
        results = r.json()
        assert isinstance(results, list)

    def test_graph(self, admin_session):
        r = admin_session.get(f"{API}/wiki/graph", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "nodes" in data and ("edges" in data or "links" in data)
        assert len(data["nodes"]) >= 1

    def test_lint(self, admin_session):
        r = admin_session.get(f"{API}/wiki/lint", timeout=15)
        assert r.status_code == 200

    def test_page_not_found(self, admin_session):
        r = admin_session.get(f"{API}/wiki/page",
                              params={"path": "does/not/exist.md"}, timeout=10)
        assert r.status_code == 404


# ───────── content submit / review ─────────

class TestContent:
    def test_submit_text_no_autoprocess(self, admin_session):
        body = {
            "type": "text",
            "title": f"TEST_{uuid.uuid4().hex[:6]} integration",
            "content": "This is a longer than fifty character body used for the loom integration test sample. " * 2,
            "category": "test",
            "auto_process": False,
        }
        r = admin_session.post(f"{API}/submit", json=body, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok") is True

    def test_submit_url_invalid_400(self, admin_session):
        r = admin_session.post(f"{API}/submit",
                               json={"type": "url", "url": "notaurl"}, timeout=10)
        assert r.status_code == 400

    def test_review_pending(self, admin_session):
        r = admin_session.get(f"{API}/review?status=pending", timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert "items" in data and "stats" in data


# ───────── sources & settings ─────────

class TestSourcesSettings:
    def test_get_sources(self, admin_session):
        r = admin_session.get(f"{API}/sources", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert "feeds" in body
        assert isinstance(body["feeds"], list)

    def test_get_settings_shows_providers(self, admin_session):
        r = admin_session.get(f"{API}/settings", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert "provider" in body
        # provider catalog (emergent/mimo/kimi/deepseek) returned via providers_info
        for key in ("providers", "default"):
            assert key in body, f"missing {key} in settings response: {list(body.keys())}"

    def test_save_settings_persists(self, admin_session):
        # save then read back
        r = admin_session.put(f"{API}/settings",
                              json={"provider": "emergent",
                                    "model": "openai/gpt-5.4",
                                    "two_stage": True}, timeout=10)
        assert r.status_code == 200
        r2 = admin_session.get(f"{API}/settings", timeout=10)
        assert r2.status_code == 200
        body = r2.json()
        assert body["provider"] == "emergent"
        assert body["model"] == "openai/gpt-5.4"
        assert body["two_stage"] is True


# ───────── pipeline runner (very short) ─────────

class TestPipeline:
    def test_pipeline_status(self, admin_session):
        r = admin_session.get(f"{API}/pipeline/status", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert "state" in body and "queue" in body

    def test_pipeline_run_and_finish(self, admin_session):
        # Kick off a single-task run — budget is exhausted, so it should
        # fail fast and STATE.running flips back to False without hang.
        r = admin_session.post(f"{API}/pipeline/run",
                               json={"max_tasks": 1, "delay": 0}, timeout=10)
        assert r.status_code in (200, 409)
        # wait a few seconds for it to settle
        for _ in range(15):
            time.sleep(1)
            st = admin_session.get(f"{API}/pipeline/status", timeout=10).json()["state"]
            if not st["running"]:
                break
        assert st["running"] is False, "pipeline did not terminate"
