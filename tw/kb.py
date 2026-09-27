"""Read-only access to the fictional firm's knowledge base (data/firm/kb.json).

What the agents may see goes through the MCP server (tw.mcp_servers.firm_kb), which exposes only the
functions below. Certifications the firm does NOT hold are never returned to agents; they exist so the
verifier can recognise a false certification claim.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from tw.config import FIRM

KB_PATH = FIRM / "kb.json"


@lru_cache(maxsize=4)
def load_kb(path: Path = KB_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _records(kb: dict) -> dict[str, dict]:
    out = {kb["firm"]["id"]: kb["firm"]}
    for section in ("certifications_held", "references", "consultants", "methods"):
        for r in kb[section]:
            out[r["id"]] = r
    for p in kb["firm"].get("partners", []):
        out[p["id"]] = p
    return out


def record_ids(kb: dict | None = None) -> set[str]:
    return set(_records(kb or load_kb()))


def get_record(record_id: str, kb: dict | None = None) -> dict | None:
    """One record by id (FIRM, CERT-*, REF-*, CV-*, MET-*, PART-*). Unknown or non-public ids give None."""
    return _records(kb or load_kb()).get(record_id.strip().upper())


def list_certifications(kb: dict | None = None) -> list[dict]:
    """Certifications the firm holds (id, name, scope, validity)."""
    return list((kb or load_kb())["certifications_held"])


def known_certifications(kb: dict | None = None) -> dict[str, bool]:
    """Every certification name the verifier knows about -> whether the firm holds it."""
    kb = kb or load_kb()
    known = {c["name"]: True for c in kb["certifications_held"]}
    known.update({c["name"]: False for c in kb["certifications_not_held"]})
    return known


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", fold(text)) if len(t) > 2}


def _score(query: str, record: dict) -> int:
    return len(_tokens(query) & _tokens(json.dumps(record, ensure_ascii=False)))


def search_references(query: str, k: int = 5, kb: dict | None = None) -> list[dict]:
    """Past references ranked by keyword overlap with the query (sector, services, technologies)."""
    refs = (kb or load_kb())["references"]
    ranked = sorted(refs, key=lambda r: (-_score(query, r), r["id"]))
    return [r for r in ranked[:k] if _score(query, r) > 0]


def search_consultants(query: str, k: int = 5, kb: dict | None = None) -> list[dict]:
    """Consultant profiles ranked by keyword overlap with the query (role, skills, certifications)."""
    cvs = (kb or load_kb())["consultants"]
    ranked = sorted(cvs, key=lambda r: (-_score(query, r), r["id"]))
    return [r for r in ranked[:k] if _score(query, r) > 0]


# The knowledge base is small, so agents can list whole collections. Keyword search alone missed evidence:
# the records are in English, most tenders are French, and methods had no search at all (M1 finding).
def list_methods(kb: dict | None = None) -> list[dict]:
    """How the firm works (application maintenance, agile, security, personal data, accessibility, ...)."""
    return list((kb or load_kb())["methods"])


def list_consultants(kb: dict | None = None) -> list[dict]:
    """Consultant summaries: id, role, years of experience, skills."""
    return [{k: c.get(k) for k in ("id", "role", "years", "skills", "certifications")} for c in (kb or load_kb())["consultants"]]


def list_references(kb: dict | None = None) -> list[dict]:
    """Reference summaries: id, sector, client type, year, services."""
    return [{k: r.get(k) for k in ("id", "sector", "client_type", "year", "services")} for r in (kb or load_kb())["references"]]
