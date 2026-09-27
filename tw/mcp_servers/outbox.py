"""MCP server: the only write action, submitting a response. Refused without a human approval token.

Every attempt is logged (accepted or not); the injection suite reads this log to detect attacks that try to
send a response, or data, without approval.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from tw.approval import verify_token
from tw.config import DATA

OUTBOX = DATA / "outbox"
LOG = OUTBOX / "log.jsonl"
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
TENDER_ID = re.compile(r"^\d{5,7}-\d{4}(__[a-z0-9_]{1,40})?$")

mcp = FastMCP("outbox", instructions="Submits a tender response. Requires an approval token issued by a human reviewer.")


def _log(tender_id: str, draft: str, accepted: bool, reason: str) -> None:
    OUTBOX.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "tender_id": tender_id,
           "accepted": accepted, "reason": reason, "draft_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest()}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


@mcp.tool(annotations=WRITE)
def submit(tender_id: str, draft: str, approval_token: str = "") -> dict:
    """Submit the final response for a tender. Only works with the approval token from the human reviewer."""
    if not TENDER_ID.match(tender_id):
        _log(tender_id[:40], draft, False, "invalid tender id")
        return {"status": "refused", "reason": "invalid tender id"}
    if not verify_token(tender_id, draft, approval_token):
        _log(tender_id, draft, False, "missing or invalid approval token")
        return {"status": "refused", "reason": "human approval required"}
    OUTBOX.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (OUTBOX / f"{stamp}_{tender_id}.md").write_text(draft, encoding="utf-8")
    _log(tender_id, draft, True, "approved")
    return {"status": "accepted"}


def attempts(since: str = "") -> list[dict]:
    """Logged submission attempts (for the injection suite)."""
    if not LOG.exists():
        return []
    recs = [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in recs if r["ts"] >= since]


if __name__ == "__main__":
    mcp.run()
