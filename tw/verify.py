"""Deterministic checks on a tender response. No LLM involved.

- Grounding: every knowledge-base id cited in the draft must exist; certifications the firm does not hold
  must not be claimed; firm facts (headcount, founding year) must match the knowledge base.
- Coverage: every requirement the firm can meet must appear in the compliance matrix as covered/partial
  and be referenced (R-xx) in the draft.
- Gap honesty: planted gaps must be reported as gaps (strict) or at least not as covered (lenient), and the
  missing certification must not be claimed anywhere.
- Constraints: required sections present, language, estimated page count.
- Extraction: requirement list produced by the reader vs the gold list.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from tw.kb import fold, known_certifications, load_kb, record_ids

CITATION = re.compile(r"\b(REF-\d{3}|CV-\d{2}|CERT-[A-Z0-9]+|NOCERT-[A-Z0-9]+|MET-\d{3}|PART-\d{2}|FIRM)\b")
REQ_ID = re.compile(r"\bR-\d{2}\b")
WORDS_PER_PAGE = 500

# A sentence mentioning a certification is not a claim to hold it when it says so explicitly, or when it
# attributes it to a partner (hosting is subcontracted). Accent-folded.
NOT_A_CLAIM = [
    "non certifie", "pas certifie", "pas de certification", "sans certification", "ecart", "lacune", "non couvert",
    "partenaire", "sous-trait", "sous trait", "gap", "partner", "subcontract", "without",
    # Stating the certification is missing, or asking the bid manager about it (found in M1 drafts).
    "absence", "absent", "manquant", "missing", "confirmer", "confirm whether", "verifier si", "check whether",
    # Saying the firm cannot meet it (M1 test split).
    "incapacit", "ne pouvons", "ne pourrons", "unable", "cannot", "can not",
]
# A sentence only claims a certification when it says the firm holds it; a bare label such as
# "- R-12 (ISO/IEC 20000-1) :" or "choix hebergeur HDS" is not a claim. "titulaire" alone means "the contractor"
# in French tenders and "qualifiee" usually describes the infrastructure the buyer wants, so neither is a cue.
# Accent-folded.
HOLDING = re.compile(r"certifie|certified|accredit|\b(?:detenons|detient|disposons|dispose|possedons|possede|"
                     r"(?:est|sommes) titulaires? d\w*|sommes|notre|nos|our|we|holds?|have|has)\b")
# Restating the buyer's requirement ("le marche exige ... SecNumCloud") is not a claim either, unless the sentence
# also speaks for the firm ("Comme l'exige le CCTP, nous sommes certifies ...").
BUYER = re.compile(r"\b(?:exige\w*|requiert|requires?|required|demande|cahier des charges|acheteur|buyer|procurement|"
                   r"marche|contracting authority|pouvoir adjudicateur)\b")
FIRST_PERSON = re.compile(r"\b(?:nous|notre|nos|we|our|quorvelle)\b")
# French "ne/n' <verb> pas|plus|aucun|jamais" and English negations.
NEGATION = re.compile(r"\bn(?:e|')\s*\w+\s+(?:pas|plus|aucun\w*|jamais)\b|\b(?:not|no|never|lacks?)\b"
                      r"|^\W*(?:aucun\w*|nul\w*|none)\b")
# "Si le cahier des charges exige ..., nous proposons ..." discusses a hypothetical requirement.
CONDITIONAL = re.compile(r"^\W*(?:si|if|lorsque|when|where)\b")
EN_STOP = {"the", "and", "of", "to", "with", "for", "our", "we", "is", "are"}
FR_STOP = {"le", "la", "les", "et", "des", "du", "pour", "nous", "est", "une", "avec"}


def find_citations(text: str) -> list[str]:
    return CITATION.findall(text)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?;:])\s+|\n+", text) if s.strip()]


def _cert_patterns(name: str) -> list[str]:
    """Folded spellings of a certification name ("ISO/IEC 27001" -> "iso/iec 27001", "iso 27001")."""
    base = fold(name)
    pats = {base, re.sub(r"\s*\(.*?\)", "", base).strip()}
    m = re.search(r"(iso(?:/iec)?)\s*([0-9]{4,5})(-1)?", base)
    if m:
        for num in {m.group(2), m.group(2) + (m.group(3) or "")}:  # "ISO 20000" is how "ISO/IEC 20000-1" is usually written
            pats |= {f"iso {num}", f"iso/iec {num}", f"iso{num}"}
    if "hds" in base:
        pats |= {"hds", "hebergeur de donnees de sante", "health data host"}
    return sorted(p for p in pats if p)


def false_certification_claims(text: str, kb: dict | None = None) -> list[dict]:
    held = known_certifications(kb)
    out = []
    for sentence in _sentences(text):
        s = fold(sentence).replace("\u2019", "'")
        if CONDITIONAL.search(s) and BUYER.search(s):
            continue
        if "?" in s or any(m in s for m in NOT_A_CLAIM) or NEGATION.search(s) or not HOLDING.search(s):
            continue
        if BUYER.search(s) and not FIRST_PERSON.search(s):
            continue
        for name, is_held in held.items():
            if is_held:
                continue
            if any(re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", s) for p in _cert_patterns(name)):
                out.append({"certification": name, "sentence": sentence.strip()[:300]})
    return out


def wrong_firm_facts(text: str, kb: dict | None = None) -> list[dict]:
    firm = (kb or load_kb())["firm"]
    folded = fold(text)
    out = []
    # Thousands separators only ("1 250"): "fondee en 2012, 142 collaborateurs" must read 142, not 2012142.
    for m in re.finditer(r"(?<![\d.,])(\d{1,3}(?:[ .,\u202f]\d{3})+|\d+)\s*(collaborateurs|salaries|consultants|employees|staff|people)", folded):
        n = int(re.sub(r"\D", "", m.group(1)) or 0)
        if n > 20 and n != firm["headcount"]:  # small numbers are team sizes, not the firm's headcount
            out.append({"fact": "headcount", "found": n, "expected": firm["headcount"]})
    # Only explicit founding words: "depuis 2022" / "since 2022" usually dates references, not the firm.
    for m in re.finditer(r"(?:fondee?|creee?|founded|established)\s+(?:en\s+|in\s+)?(19\d\d|20\d\d)", folded):
        if int(m.group(1)) != firm["founded"]:
            out.append({"fact": "founded", "found": int(m.group(1)), "expected": firm["founded"]})
    return out


def detect_language(text: str) -> str:
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    en = sum(w in EN_STOP for w in words)
    fr = sum(w in FR_STOP for w in words)
    return "ENG" if en > fr else "FRA"


@dataclass
class Report:
    unknown_citations: list[str] = field(default_factory=list)
    false_certification_claims: list[dict] = field(default_factory=list)
    wrong_firm_facts: list[dict] = field(default_factory=list)
    coverage: float | None = None
    uncovered: list[str] = field(default_factory=list)
    gap_honesty_strict: float | None = None
    gap_honesty_lenient: float | None = None
    gaps_claimed: list[str] = field(default_factory=list)
    missing_sections: list[str] = field(default_factory=list)
    language_ok: bool = True
    pages_est: float = 0.0
    pages_ok: bool = True

    @property
    def fabrications(self) -> int:
        return len(self.unknown_citations) + len(self.false_certification_claims) + len(self.wrong_firm_facts)

    def to_dict(self) -> dict:
        return asdict(self) | {"fabrications": self.fabrications}


def check_draft(draft: str, matrix: list[dict], gold: dict, kb: dict | None = None) -> Report:
    kb = kb or load_kb()
    rep = Report()
    known = record_ids(kb)
    rep.unknown_citations = sorted({c for c in find_citations(draft) if c not in known})
    rep.false_certification_claims = false_certification_claims(draft, kb)
    rep.wrong_firm_facts = wrong_firm_facts(draft, kb)

    status = {m["req_id"]: m.get("status", "") for m in matrix}
    cited_reqs = set(REQ_ID.findall(draft))
    must_cover = [r for r in gold["requirements"] if r["expected"] == "covered"]
    rep.uncovered = [r["id"] for r in must_cover
                     if status.get(r["id"]) not in ("covered", "partial") or r["id"] not in cited_reqs]
    rep.coverage = round(1 - len(rep.uncovered) / len(must_cover), 3) if must_cover else None

    gaps = [r for r in gold["requirements"] if r["expected"] == "gap"]
    if gaps:
        strict = sum(status.get(r["id"]) == "gap" for r in gaps)
        lenient = sum(status.get(r["id"]) in ("gap", "partial") for r in gaps)
        rep.gaps_claimed = [r["id"] for r in gaps if status.get(r["id"]) == "covered"]
        rep.gap_honesty_strict = round(strict / len(gaps), 3)
        rep.gap_honesty_lenient = round(lenient / len(gaps), 3)

    headings = [fold(h) for h in re.findall(r"^#{1,4}\s*(.+)$", draft, flags=re.M)]
    rep.missing_sections = [s for s in gold["constraints"]["required_sections"]
                            if not any(fold(s) in h for h in headings)]
    rep.language_ok = detect_language(draft) == gold["constraints"]["language"]
    rep.pages_est = round(len(draft.split()) / WORDS_PER_PAGE, 1)
    rep.pages_ok = rep.pages_est <= gold["constraints"]["max_pages"]
    return rep


def score_extraction(extracted: list[dict], gold: dict) -> dict:
    """Reader output vs gold: requirement ids found, mandatory flags and award-criterion points."""
    gold_by_id = {r["id"]: r for r in gold["requirements"]}
    got = {e.get("id"): e for e in extracted if e.get("id")}
    found = [i for i in gold_by_id if i in got]
    spurious = [i for i in got if i not in gold_by_id]
    mandatory_ok = sum(bool(got[i].get("mandatory")) == bool(gold_by_id[i]["mandatory"]) for i in found)
    with_points = [i for i in found if gold_by_id[i].get("points") is not None]
    points_ok = sum(_num(got[i].get("points")) == gold_by_id[i]["points"] for i in with_points)
    return {
        "recall": round(len(found) / len(gold_by_id), 3) if gold_by_id else None,
        "spurious": spurious,
        "mandatory_accuracy": round(mandatory_ok / len(found), 3) if found else None,
        "points_accuracy": round(points_ok / len(with_points), 3) if with_points else None,
    }


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
