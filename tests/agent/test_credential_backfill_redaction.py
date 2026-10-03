"""Backfill redaction of already-persisted tool-result credentials (HER-225).

The persist-path scrub stops NEW tool results from leaking, but rows written before
it shipped still hold cleartext secrets in state.db. ``SessionDB.redact_credentials_in_place``
rewrites those rows in place, using the same fail-closed scrubber, touching only
``content`` / ``api_content``.
"""

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    database = SessionDB(db_path=tmp_path / "state.db")
    database.create_session("s1", source="cli")
    database.create_session("s2", source="cli")
    yield database
    database.close()


def _store_tool(db, sid, content, api_content=None):
    db.append_message(sid, role="tool", content=content, api_content=api_content,
                      tool_name="read_file", tool_call_id="c1")


class TestRedactCredentialsInPlace:
    def test_dry_run_reports_but_does_not_write(self, db):
        _store_tool(db, "s1", "leaked sk-" + "A" * 20)
        report = db.redact_credentials_in_place(dry_run=True)
        assert report["rows_affected"] == 1
        # still raw on disk
        rows = db.get_messages("s1")
        assert any("A" * 20 in (r.get("content") or "") for r in rows)

    def test_apply_redacts_sk_and_pem(self, db):
        _store_tool(db, "s1", "sk-" + "A" * 20 + "\n-----BEGIN PRIVATE KEY-----\nBODY\n-----END PRIVATE KEY-----")
        report = db.redact_credentials_in_place(backup=False)
        assert report["rows_affected"] == 1
        rows = db.get_messages("s1")
        content = rows[0]["content"]
        assert "A" * 20 not in content
        assert "BODY" not in content
        assert "-----BEGIN PRIVATE KEY-----" not in content

    def test_benign_rows_untouched(self, db):
        _store_tool(db, "s1", "plain output, nothing sensitive")
        report = db.redact_credentials_in_place(backup=False)
        assert report["rows_affected"] == 0

    def test_redacts_both_content_and_api_content(self, db):
        _store_tool(db, "s1", "content sk-" + "A" * 20, api_content="api ghp_" + "B" * 20)
        report = db.redact_credentials_in_place(backup=False)
        assert report["rows_affected"] == 1
        content, api = db.get_messages("s1")[0]["content"], db.get_messages("s1")[0]["api_content"]
        assert "A" * 20 not in content
        assert "B" * 20 not in api

    def test_non_tool_roles_untouched(self, db):
        db.append_message("s1", role="assistant", content="sk-" + "A" * 20)
        db.append_message("s1", role="user", content="ghp_" + "B" * 20)
        report = db.redact_credentials_in_place(backup=False)
        assert report["rows_affected"] == 0

    def test_fail_closed_on_redactor_error(self, db, monkeypatch):
        _store_tool(db, "s1", "leak sk-" + "A" * 20)
        monkeypatch.setattr(
            "agent.redact.redact_sensitive_text",
            lambda text, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        report = db.redact_credentials_in_place(backup=False)
        assert report["rows_affected"] == 1
        content = db.get_messages("s1")[0]["content"]
        assert "A" * 20 not in content
        assert "[redaction-unavailable]" in content
