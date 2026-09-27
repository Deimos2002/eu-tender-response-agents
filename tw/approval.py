"""Human approval tokens.

The approval gate (a human, via a LangGraph interrupt) issues a token bound to the exact draft it approved.
The outbox refuses any submission without a valid token. Agents never see the secret, so they cannot mint a
token, and editing a draft after approval invalidates it.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from tw.config import CACHE


def _secret() -> bytes:
    env = os.environ.get("TW_APPROVAL_SECRET")
    if env:
        return env.encode()
    path = CACHE / "approval.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip().encode()


def _message(tender_id: str, draft: str) -> bytes:
    return f"{tender_id}\n{hashlib.sha256(draft.encode('utf-8')).hexdigest()}".encode()


def issue_token(tender_id: str, draft: str) -> str:
    return hmac.new(_secret(), _message(tender_id, draft), hashlib.sha256).hexdigest()


def verify_token(tender_id: str, draft: str, token: str) -> bool:
    if not token:
        return False
    return hmac.compare_digest(issue_token(tender_id, draft), token)
