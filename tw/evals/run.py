"""Run an agent on the tender set and score it with the deterministic checks of tw.verify.

    python -m tw.evals.run --agent baseline --split dev

Writes results/<agent>/<timestamp>_<split>/: rows.jsonl (one scored run per tender), summary.json and the
drafts. Model responses are cached, so re-scoring a finished run costs nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import logging
import statistics

from tw.config import ROOT, SPECS
from tw.mcp_servers import outbox
from tw.verify import check_draft, score_extraction

RESULTS = ROOT / "results"
DEV_SHARE = 30  # percent of tenders in the development split


def split_of(tender_id: str) -> str:
    return "dev" if int(hashlib.sha1(tender_id.encode()).hexdigest(), 16) % 100 < DEV_SHARE else "test"


def tender_ids(split: str = "all") -> list[str]:
    ids = sorted(p.name.removesuffix(".gold.json") for p in SPECS.glob("*.gold.json"))
    return ids if split == "all" else [i for i in ids if split_of(i) == split]


def load_gold(tender_id: str) -> dict:
    return json.loads((SPECS / f"{tender_id}.gold.json").read_text(encoding="utf-8"))


def score(result, gold: dict, submit_attempts: int) -> dict:
    rep = check_draft(result.draft, result.matrix, gold).to_dict()
    ext = score_extraction(result.requirements, gold)
    n_gaps = sum(r["expected"] == "gap" for r in gold["requirements"])
    return {
        "tender_id": result.tender_id, "language": gold["language"], "split": split_of(result.tender_id),
        "finished": result.finished, "error": result.error,
        "coverage": rep["coverage"], "uncovered": rep["uncovered"],
        "fabrications": rep["fabrications"], "unknown_citations": rep["unknown_citations"],
        "false_certification_claims": rep["false_certification_claims"], "wrong_firm_facts": rep["wrong_firm_facts"],
        "n_gaps": n_gaps, "gap_honesty_strict": rep["gap_honesty_strict"], "gap_honesty_lenient": rep["gap_honesty_lenient"],
        "gaps_claimed": rep["gaps_claimed"],
        "missing_sections": rep["missing_sections"], "language_ok": rep["language_ok"], "pages_est": rep["pages_est"],
        "pages_ok": rep["pages_ok"], "extraction": ext, "submit_attempts": submit_attempts,
        "steps": result.steps, "tool_calls": len(result.tool_calls), "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens, "cost_usd": result.cost_usd, "latency_s": result.latency_s,
        "model_latency_s": round(result.model_latency_s, 1),
        "model": result.model, "questions": len(result.questions),
        "interrupted": result.interrupted, "revisions": result.revisions, "open_issues": result.open_issues,
        "tokens_by_node": result.tokens_by_node,
    }


def _mean(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(statistics.fmean(vals), 3) if vals else None


def summarise(rows: list[dict]) -> dict:
    done = [r for r in rows if r["finished"]]
    gap_rows = [r for r in done if r["n_gaps"]]
    total_gaps = sum(r["n_gaps"] for r in gap_rows)
    return {
        "n": len(rows), "finished": len(done), "errors": [r["error"] for r in rows if r["error"]],
        "coverage_mean": _mean(done, "coverage"),
        "fabrications_total": sum(r["fabrications"] for r in done),
        "tenders_with_fabrication": sum(r["fabrications"] > 0 for r in done),
        "false_certification_claims": sum(len(r["false_certification_claims"]) for r in done),
        "gap_honesty_strict": round(sum(r["gap_honesty_strict"] * r["n_gaps"] for r in gap_rows) / total_gaps, 3) if total_gaps else None,
        "gap_honesty_lenient": round(sum(r["gap_honesty_lenient"] * r["n_gaps"] for r in gap_rows) / total_gaps, 3) if total_gaps else None,
        "gaps_claimed_as_covered": sum(len(r["gaps_claimed"]) for r in done),
        "planted_gaps": total_gaps,
        "sections_complete": sum(not r["missing_sections"] for r in done),
        "language_ok": sum(r["language_ok"] for r in done),
        "extraction_recall_mean": round(statistics.fmean(r["extraction"]["recall"] for r in done), 3) if done else None,
        "submit_attempts": sum(r["submit_attempts"] for r in rows),
        "cost_usd_total": round(sum(r["cost_usd"] for r in rows), 4), "cost_usd_mean": _mean(rows, "cost_usd"),
        "tokens_mean": round(statistics.fmean(r["input_tokens"] + r["output_tokens"] for r in rows)) if rows else None,
        "latency_s_mean": _mean(rows, "latency_s"), "model_latency_s_mean": _mean(rows, "model_latency_s"), "tool_calls_mean": _mean(rows, "tool_calls"),
    }


async def evaluate(agent: str, ids: list[str], llm) -> list[tuple[dict, str]]:
    from tw.agents.baseline import run_baseline
    from tw.agents.multi import run_multi

    runners = {"baseline": run_baseline, "multi": run_multi}
    out = []
    for i, tid in enumerate(ids, 1):
        before = len(outbox.attempts())  # count new log entries: timestamps have one-second resolution
        result = await runners[agent](tid, llm)
        attempts = [a for a in outbox.attempts()[before:] if a["tender_id"].startswith(tid)]
        row = score(result, load_gold(tid), len(attempts))
        print(f"[{i}/{len(ids)}] {tid} finished={row['finished']} coverage={row['coverage']} fabrications={row['fabrications']} "
              f"gaps={row['gap_honesty_strict']} cost=${row['cost_usd']:.4f} {row['latency_s']}s {row['error'] or ''}", flush=True)
        out.append((row, result.draft))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["baseline", "multi"], default="baseline")
    ap.add_argument("--split", choices=["dev", "test", "all"], default="dev")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tenders", help="comma-separated tender ids (overrides --split)")
    args = ap.parse_args()
    logging.getLogger("mcp").setLevel(logging.WARNING)

    from tw.llm import from_env

    llm = from_env()
    ids = args.tenders.split(",") if args.tenders else tender_ids(args.split)
    ids = ids[: args.limit] if args.limit else ids
    results = asyncio.run(evaluate(args.agent, ids, llm))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS / args.agent / f"{stamp}_{args.split if not args.tenders else 'custom'}"
    (out / "drafts").mkdir(parents=True)
    rows = [r for r, _ in results]
    with open(out / "rows.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for r, draft in results:
        (out / "drafts" / f"{r['tender_id']}.md").write_text(draft, encoding="utf-8")
    summary = summarise(rows) | {"agent": args.agent, "model": llm.model, "split": args.split, "generated_at": stamp}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    print(f"-> {out}")


if __name__ == "__main__":
    main()
