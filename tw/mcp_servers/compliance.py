"""MCP server: Project A (GDPR / AI Act compliance copilot) as a read-only tool.

Answers come with verbatim citations of the regulation, checked by Project A's verifier. The deployed API
scales to zero, so the first call can take ~30 s.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from tw.config import CACHE

DEFAULT_URL = "https://copilot-api.ashyisland-fb3eded8.italynorth.azurecontainerapps.io"
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)

mcp = FastMCP("compliance", instructions="GDPR and EU AI Act questions, answered from the official texts with verified citations.")


def _url() -> str:
    return os.environ.get("COMPLIANCE_API_URL", DEFAULT_URL).rstrip("/")


def _cache() -> sqlite3.Connection:
    """Answers are cached on disk: evaluation reruns neither call Project A again nor spend its daily budget."""
    CACHE.mkdir(exist_ok=True)
    db = sqlite3.connect(CACHE / "compliance.sqlite")
    db.execute("CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, value TEXT)")
    return db


@mcp.tool(annotations=READ_ONLY)
def ask(question: str, lang: str = "fr") -> dict:
    """Ask a GDPR or AI Act question. Returns an answer with verified quotes of the regulation."""
    key = hashlib.sha256(json.dumps([_url(), question[:2000], lang]).encode()).hexdigest()
    db = _cache()
    row = db.execute("SELECT value FROM answers WHERE key = ?", (key,)).fetchone()
    if row:
        return json.loads(row[0])
    try:
        r = httpx.post(f"{_url()}/api/ask", json={"question": question[:2000], "lang": lang}, timeout=120)
        r.raise_for_status()
        d = r.json()
    except httpx.HTTPError as exc:
        return {"status": "error", "error": str(exc)[:300]}
    out = {
        "status": d.get("status"),
        "answer": d.get("answer"),
        "citations": [{"ref": c.get("ref") or c.get("source_id"), "quote": c.get("quote")} for c in d.get("citations", [])],
        "disclosure": (d.get("disclosure") or {}).get(lang),
    }
    if out["status"] not in ("error", "unavailable"):
        db.execute("INSERT OR REPLACE INTO answers VALUES (?, ?)", (key, json.dumps(out, ensure_ascii=False)))
        db.commit()
    return out


if __name__ == "__main__":
    mcp.run()
