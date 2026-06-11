"""Phase 2 — task detail / wiki edit / schedule / weekly digest tests."""
import os
import re
import uuid
from datetime import date
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")
BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api"
WIKI_ROOT = Path("/app/wiki")

ADMIN_EMAIL = "admin@loom.dev"
ADMIN_PASSWORD = "LoomAdmin2026!"


@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
               timeout=15)
    assert r.status_code == 200, r.text
    return s


# ───────── auth gate on phase-2 endpoints ─────────

class TestAuthGate:
    def test_schedule_requires_auth(self):
        r = requests.get(f"{API}/schedule", timeout=10)
        assert r.status_code == 401

    def test_digest_preview_requires_auth(self):
        r = requests.get(f"{API}/digest/preview", timeout=10)
        assert r.status_code == 401

    def test_digest_send_requires_auth(self):
        r = requests.post(f"{API}/digest/send", json={}, timeout=10)
        assert r.status_code == 401


# ───────── task detail ─────────

class TestTaskDetail:
    def test_404_for_missing(self, admin_session):
        r = admin_session.get(f"{API}/tasks/99999999", timeout=10)
        assert r.status_code == 404
        assert "任务不存在" in r.json().get("detail", "")

    def test_existing_task_detail_shape(self, admin_session):
        # find a real task id
        r = admin_session.get(f"{API}/tasks?limit=1", timeout=15)
        assert r.status_code == 200
        items = r.json().get("items", [])
        if not items:
            pytest.skip("no tasks present")
        tid = items[0]["id"]
        d = admin_session.get(f"{API}/tasks/{tid}", timeout=10)
        assert d.status_code == 200
        body = d.json()
        assert "task" in body and "result" in body
        t = body["task"]
        for k in ("id", "filepath", "status", "retry_count",
                  "input_tokens", "output_tokens"):
            assert k in t, f"missing task.{k}"
        assert t["id"] == tid


# ───────── wiki edit ─────────

class TestWikiEdit:
    def test_edit_missing_page_404(self, admin_session):
        r = admin_session.put(
            f"{API}/wiki/page",
            json={"path": "ideas/__definitely_not_a_page__",
                  "body": "x"}, timeout=10)
        assert r.status_code == 404

    def test_edit_preserves_frontmatter_and_bumps_updated(self, admin_session):
        # find any existing wiki page through tree
        tree = admin_session.get(f"{API}/wiki/tree", timeout=10).json()
        target = None
        for section, pages in tree.items():
            if pages:
                target = pages[0]["path"]
                break
        if not target:
            pytest.skip("no wiki pages to edit")

        # fetch current body
        cur = admin_session.get(f"{API}/wiki/page",
                                params={"path": target}, timeout=10).json()
        original_body = cur["body"]
        marker = f"<!-- TEST_EDIT_{uuid.uuid4().hex[:8]} -->"
        new_body = original_body + "\n\n" + marker

        # save
        r = admin_session.put(f"{API}/wiki/page",
                              json={"path": target, "body": new_body}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True

        # verify file frontmatter preserved + updated bumped to today
        fp = WIKI_ROOT / f"{target}.md"
        assert fp.exists()
        text = fp.read_text(encoding="utf-8")
        assert text.startswith("---"), "frontmatter missing"
        fm_block = text.split("---", 2)[1]
        assert "updated:" in fm_block, "updated key missing"
        today = date.today().isoformat()
        m = re.search(r"^updated:\s*([0-9-]+)", fm_block, re.M)
        assert m and m.group(1) == today, f"updated not bumped: got {m and m.group(1)}"
        # body contains marker
        assert marker in text

        # restore original body to keep seed clean
        admin_session.put(f"{API}/wiki/page",
                          json={"path": target, "body": original_body}, timeout=15)


# ───────── schedule ─────────

class TestSchedule:
    def test_get_schedule_shape(self, admin_session):
        r = admin_session.get(f"{API}/schedule", timeout=10)
        assert r.status_code == 200
        body = r.json()
        for k in ("rss_enabled", "rss_hour", "digest_enabled",
                  "digest_weekday", "digest_hour", "jobs", "email_configured"):
            assert k in body, f"missing {k}"
        assert "rss_job" in body["jobs"] and "digest_job" in body["jobs"]
        # phase-2 spec: RESEND_API_KEY not set
        assert body["email_configured"] is False

    def test_enable_then_disable_jobs(self, admin_session):
        # enable
        r = admin_session.put(f"{API}/schedule", json={
            "rss_enabled": True, "rss_hour": 6, "auto_pipeline": True,
            "pipeline_max": 20,
            "digest_enabled": True, "digest_weekday": 0, "digest_hour": 9,
            "digest_recipients": [],
        }, timeout=10)
        assert r.status_code == 200, r.text
        jobs = r.json()["jobs"]
        assert jobs["rss_job"] is not None, "rss_job next run should be set"
        assert jobs["digest_job"] is not None, "digest_job next run should be set"
        # confirm via GET
        g = admin_session.get(f"{API}/schedule", timeout=10).json()
        assert g["rss_enabled"] is True and g["digest_enabled"] is True
        assert g["jobs"]["rss_job"] is not None
        assert g["jobs"]["digest_job"] is not None

        # disable
        r2 = admin_session.put(f"{API}/schedule", json={
            "rss_enabled": False, "rss_hour": 6, "auto_pipeline": True,
            "pipeline_max": 20,
            "digest_enabled": False, "digest_weekday": 0, "digest_hour": 9,
            "digest_recipients": [],
        }, timeout=10)
        assert r2.status_code == 200
        jobs2 = r2.json()["jobs"]
        assert jobs2["rss_job"] is None
        assert jobs2["digest_job"] is None


# ───────── digest ─────────

class TestDigest:
    def test_preview_returns_html_and_data(self, admin_session):
        r = admin_session.get(f"{API}/digest/preview", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "html" in body and "data" in body
        html = body["html"]
        # LOOM_ wordmark and stat cards
        assert "LOOM" in html and "每周知识简报" in html
        assert "本周编译文章" in html
        d = body["data"]
        for k in ("total", "categories", "tokens", "review_pending", "items"):
            assert k in d, f"missing data.{k}"
        assert isinstance(d["categories"], dict)
        assert isinstance(d["total"], int)

    def test_send_without_resend_returns_400(self, admin_session):
        r = admin_session.post(f"{API}/digest/send", json={}, timeout=15)
        # spec: RESEND_API_KEY empty → 400 with RESEND_API_KEY message
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
        detail = r.json().get("detail", "")
        assert "RESEND_API_KEY" in detail
