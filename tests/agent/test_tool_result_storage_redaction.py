"""Storage-boundary redaction of tool RESULT content in the SessionDB flush path.

HER-225: 297 of 332 credential-bearing state.db messages were ``role: tool`` — the
persist path wrote tool output verbatim, so a secret pasted a turn ago could re-enter
model context on session resume. The fix scrubs the *durable* copy of every tool result
with ``redact_sensitive_text(force=True)`` (fail-closed) while leaving the in-memory message —
and every other role — untouched.
"""

import pytest

from agent.session_persistence import (
    _db_flush_row,
    _durable_tool_result_content,
)


@pytest.fixture(autouse=True)
def _ensure_redaction_enabled(monkeypatch):
    """Mirror test_redact.py: never let a prior import disable redaction."""
    monkeypatch.delenv("HERMES_REDACT_SECRETS", raising=False)
    monkeypatch.setattr("agent.redact._REDACT_ENABLED", True)


def _tool_msg(content):
    return {
        "role": "tool",
        "content": content,
        "tool_name": "read_file",
        "tool_call_id": "call-1",
    }


class TestDurableToolResultRedaction:
    def test_prefix_key_masked(self):
        secret = "sk-" + "A" * 20
        out = _durable_tool_result_content(f"leaked {secret} here")
        assert secret not in out
        # display mask keeps a short head/tail for debuggability, never the body
        assert "A" * 20 not in out

    def test_github_and_aws_token_masked(self):
        out = _durable_tool_result_content(
            "ghp_" + "B" * 20 + " AKIA" + "C" * 16
        )
        assert "B" * 20 not in out
        assert "C" * 16 not in out

    def test_pem_block_masked(self):
        block = "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----"
        out = _durable_tool_result_content(block)
        # the base64 body is gone entirely; the header/footer are replaced by a marker
        assert "AAAA" not in out
        assert "-----BEGIN PRIVATE KEY-----" not in out
        assert "-----END PRIVATE KEY-----" not in out

    def test_benign_output_unchanged(self):
        text = "total 42\n-rw-r--r-- 1 hermes staff 12 Oct 2 10:00 foo.txt"
        assert _durable_tool_result_content(text) == text

    def test_non_string_multimodal_list_projected_not_redacted(self):
        assert _durable_tool_result_content([{"type": "text", "text": "plain"}]) == "plain"

    def test_in_memory_message_not_mutated(self):
        """Only the durable row is scrubbed; the live transcript keeps the raw value
        for the current turn's replay (the #43083 invariant)."""
        msg = _tool_msg("sk-" + "D" * 20)
        row = _db_flush_row(None, msg, False)
        assert "D" * 20 in msg["content"]       # live message untouched
        assert "D" * 20 not in row["content"]    # durable row scrubbed

    def test_assistant_content_not_scrubbed_at_this_seam(self):
        """Only tool results are scrubbed here; assistant content is redacted
        upstream and tool-call arguments must survive verbatim."""
        row = _db_flush_row(None, {"role": "assistant", "content": "sk-" + "E" * 20}, False)
        assert "E" * 20 in row["content"]

    def test_fail_closed_on_redactor_error(self, monkeypatch):
        """A redactor that raises must yield the sentinel, never the raw secret."""
        monkeypatch.setattr(
            "agent.redact.redact_sensitive_text",
            lambda text, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        secret = "sk-" + "F" * 20
        out = _durable_tool_result_content(f"leak {secret}")
        assert secret not in out
        assert "[redaction-unavailable]" in out
