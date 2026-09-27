"""Fetch and normalise real tender notices from TED (Tenders Electronic Daily).

The notice is the *frame* of a tender: title, buyer, lot, deadline, duration, value and, when the buyer
filled them in, the award criteria with their points. These structured fields are the gold standard for
the extraction step. The full specification is synthetic (see tw.specs).

Reuse: TED notices may be reused freely with attribution (Commission Decision 2011/833/EU).

    python -m tw.ted            # refresh data/tenders/ (asks the TED search API, anonymous)
"""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path

import httpx

from tw.config import RAW, TENDERS

API = "https://api.ted.europa.eu/v3/notices/search"
ATTRIBUTION = "Source: TED — Tenders Electronic Daily (ted.europa.eu), © European Union, reused under Decision 2011/833/EU."
CPV = "72000000 72200000 72220000 72224000 72260000 72267000 72300000 72500000 79411000"
FIELDS = [
    "publication-number", "publication-date", "official-language", "buyer-name", "buyer-country",
    "title-proc", "description-proc", "identifier-lot", "title-lot", "description-lot",
    "deadline-receipt-tender-date-lot", "duration-period-value-lot", "duration-period-unit-lot",
    "estimated-value-proc", "estimated-value-cur-proc", "classification-cpv",
    "award-criterion-type-lot", "award-criterion-number-lot", "award-criterion-number-weight-lot",
    "award-criterion-description-lot",
]
LANG_KEYS = {"FRA": "fra", "ENG": "eng"}


def search(query: str, limit: int = 100, page: int = 1, *, client: httpx.Client | None = None) -> dict:
    """One page of the TED search API; raw responses are cached under data/raw/ted/."""
    body = {"query": query, "fields": FIELDS, "limit": limit, "page": page}
    key = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    cache = RAW / "ted" / f"{key}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    http = client or httpx.Client(timeout=90)
    r = http.post(API, json=body)
    r.raise_for_status()
    data = r.json()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


MAIN_CPV_PREFIXES = ("72", "48", "79411")  # IT services, software, management consulting


def _clean(s: str) -> str:
    # Some notices are HTML-escaped twice ("Sign&amp;amp;Go").
    return html.unescape(html.unescape(s)).strip()


def _text(value, lang: str):
    """Pick the notice-language version of a multilingual field ({"fra": ...}), unescaped."""
    if isinstance(value, dict):
        value = value.get(lang)
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, list):
        return [_clean(v) if isinstance(v, str) else v for v in value]
    return value


def _first(value):
    return value[0] if isinstance(value, list) and value else value


def normalise(notice: dict) -> dict | None:
    """Flatten a single-lot notice into our schema; None if it cannot be used as a tender frame."""
    langs = notice.get("official-language") or []
    lang_code = next((code for code in langs if code in LANG_KEYS), None)
    if lang_code is None or len(notice.get("identifier-lot") or []) != 1:
        return None
    lang = LANG_KEYS[lang_code]
    types = notice.get("award-criterion-type-lot") or []
    points = notice.get("award-criterion-number-lot") or []
    descs = _text(notice.get("award-criterion-description-lot"), lang) or []
    criteria = []
    if types and len(types) == len(points) == len(descs):
        criteria = [{"type": t, "points": float(p), "description": d.strip()} for t, p, d in zip(types, points, descs, strict=True)]
    deadline = _first(notice.get("deadline-receipt-tender-date-lot"))
    duration = _first(notice.get("duration-period-value-lot"))
    pub = notice["publication-number"]
    value = notice.get("estimated-value-proc")
    return {
        "id": pub,
        "language": lang_code,
        "title": _text(notice.get("title-proc"), lang),
        "buyer": _first(_text(notice.get("buyer-name"), lang)),
        "buyer_country": _first(notice.get("buyer-country")),
        "description": _text(notice.get("description-proc"), lang),
        "lot_title": _first(_text(notice.get("title-lot"), lang)),
        "lot_description": _first(_text(notice.get("description-lot"), lang)),
        "deadline": deadline[:10] if deadline else None,
        "duration": {"value": int(duration), "unit": _first(notice.get("duration-period-unit-lot"))} if duration else None,
        "estimated_value": {"amount": float(value), "currency": notice.get("estimated-value-cur-proc")} if value else None,
        "cpv_main": _first(notice.get("classification-cpv")),
        "cpv": sorted(set(notice.get("classification-cpv") or [])),
        "award_criteria": criteria,
        "publication_date": (notice.get("publication-date") or "")[:10],
        "source_url": f"https://ted.europa.eu/{lang_code[:2].lower()}/notice/-/detail/{pub}",
        "attribution": ATTRIBUTION,
    }


def usable(t: dict) -> bool:
    """A good tender frame for an IT consulting firm: main CPV in IT/software/consulting, text in the notice
    language, a deadline, a duration and weighted award criteria."""
    in_scope = str(t.get("cpv_main") or "").startswith(MAIN_CPV_PREFIXES)
    return bool(in_scope and t["title"] and t["description"] and t["deadline"] and t["duration"] and len(t["award_criteria"]) >= 2)


def select(notices: list[dict], n: int) -> list[dict]:
    """Deterministic pick: usable frames, ordered by publication number."""
    frames = [t for t in (normalise(x) for x in notices) if t and usable(t)]
    frames.sort(key=lambda t: t["id"])
    return frames[:n]


def snapshot(n_fr: int = 18, n_en: int = 6, since: str = "20260701") -> list[dict]:
    base = f"classification-cpv IN ({CPV}) AND notice-type IN (cn-standard) AND publication-date>={since} AND award-criterion-number-lot=*"
    picked = []
    for lang, n in (("FRA", n_fr), ("ENG", n_en)):
        notices = []
        for page in (1, 2, 3):
            notices += search(f"{base} AND official-language IN ({lang})", page=page).get("notices", [])
        picked += select(notices, n)
    TENDERS.mkdir(parents=True, exist_ok=True)
    for old in TENDERS.glob("*.json"):
        old.unlink()
    for t in picked:
        (TENDERS / f"{t['id']}.json").write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    return picked


def load_all(folder: Path = TENDERS) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


if __name__ == "__main__":
    got = snapshot()
    by_lang: dict[str, int] = {}
    for t in got:
        by_lang[t["language"]] = by_lang.get(t["language"], 0) + 1
    print(f"{len(got)} tenders -> {TENDERS}  {by_lang}")
