# 05 — M2: multi-agent graph vs single-agent baseline

Same model (`gpt-5-mini`, reasoning effort `low`), same tools and knowledge base, same frozen checks, same 19
test tenders (never used for tuning). Runs: `results/baseline/20260927-030114_test`,
`results/multi/20260927-031520_test`.

| Metric (test, 19 tenders) | Single agent | Multi-agent graph |
|---|---:|---:|
| Runs finished | 19 | 19 |
| Stopped at the human approval step | n/a | 19 |
| Requirement coverage | 0.953 | **0.984** |
| All required sections present | 13 / 19 | **19 / 19** |
| Planted gaps reported as gaps | 13 / 13 | 13 / 13 |
| False claims of holding a certification (hand-verified) | 0 | 0 |
| Unsupported commitments (hand-verified) | 0 | 2 |
| Submission attempts without approval | 0 | 0 |
| Extraction recall / correct language | 1.00 / 19 | 1.00 / 19 |
| Cost per tender (list price) | $0.017 | **$0.012** |
| Tokens per tender | 35.4k | **16.0k** |
| Tool calls per tender | 10.7 | 5.9 |
| Model time per tender | **30 s** | 44 s |

## Reading the results

- **The graph is cheaper and more complete.** Each node gets only what it needs (the writer receives the
  evidence records, not the whole tool history), so it uses 55 % fewer tokens and 29 % less money per tender,
  while coverage rises from 0.95 to 0.98 and every draft has all required sections (the deterministic
  reviewer sends a draft back when a heading is missing). It is slower because its steps run one after the
  other.
- **Honesty is equal.** Both report every planted gap and neither claims a certification it does not hold.
- **New failure mode in the graph: invented commitments.** In two tenders the writer promised to obtain
  ISO 14001 "within 18 months". The firm does not hold it and its knowledge base records no such plan.
  The checker flagged both sentences as certification claims; by hand they are unsupported promises, not
  claims to hold the certificate. A reviewer rule for promises about certifications the firm lacks is the
  obvious next fix.
- **Security properties hold structurally.** Only the tool-less reader sees the tender text; the researcher
  has read-only tools; `outbox.submit` is reachable only from the approval node with a human token bound to
  the draft. M3 measures what this buys under attack.

## What changed between M1 and this comparison (all on the dev split)

1. **Knowledge-base tools.** Methods (reversibility, GDPR Art. 28, eco-design) had no search tool and keyword
   search failed on French queries, so both agents marked requirements the firm can meet as gaps (M1 test
   coverage 0.85). `firm_kb` now lists methods, consultants and references in short form. Both agents were
   re-run with the new tools; the single-agent numbers above are with the new tools.
2. **Checker precision.** Negations starting with "aucun", curly apostrophes, conditional buyer requirements
   ("Si le cahier des charges exige ...") and numbers such as "2012, 142 collaborateurs" were misread. In the
   graph these false positives also made the reviewer send honest drafts back for rewriting. Regression tests
   use the real sentences.
3. **Graph rule.** Only price criteria may be out of scope; the researcher once marked a quality criterion so.

Cost of the whole Project B so far (development, both agents, dev and test): about $0.79 at list prices.
