# 04 — M1: single-agent baseline

One `gpt-5-mini` agent with every MCP tool (tenders, firm knowledge base, compliance, outbox) and a
`final_answer` tool for structured output. It reads the specification as an untrusted tool result, looks up
evidence, and returns the requirement list, compliance matrix, draft and gaps. It is told not to submit.
Code: `tw/agents/baseline.py`; runner: `python -m tw.evals.run --agent baseline --split dev|test`.

## Results

Test split: 19 tenders never used to tune the prompt or the checks (dev: 5). Reasoning effort `low`.

| Metric | Dev (5) | Test (19) |
|---|---:|---:|
| Runs finished | 5 | 19 |
| Requirement extraction recall | 1.00 | 1.00 |
| Requirement coverage | 0.98 | 0.85 |
| Fabricated firm claims (tenders affected) | 0 | 0 |
| Planted gaps reported as gaps | 3/3 | 13/13 |
| Gaps claimed as covered | 0 | 0 |
| All required sections present | 5/5 | 17/19 |
| Correct language | 5/5 | 19/19 |
| Submission attempts without approval | 0 | 0 |
| Cost per tender (list price) | $0.012 | $0.014 |
| Tokens / model time per tender | 19.5k / 35 s | 25.4k / 36 s |

## Findings

- The baseline errs on the side of caution, not invention: no false claims, every planted gap reported. On
  the test split, 20 of 21 coverage misses were requirements the firm can meet (reversibility plan, GDPR
  processor terms, team lead, eco-design) that the agent marked as gaps because it did not find the evidence
  in the knowledge base (the eco-design record `MET-006` exists, for example). A dedicated research step in
  M2 should improve this.
- Two test drafts wrote section titles as plain lines instead of Markdown headings.

## Measurement fixes (disclosed)

Every automatic "fabrication" flag was read by hand before being trusted.

1. Dev, before the test run: all 12 flags were false positives (requirement labels such as
   "- R-12 (ISO/IEC 20000-1) :", questions to the bid manager, "absence de certification", and "depuis 2022"
   read as a founding year). The checker now requires a sentence to say the firm holds the certification,
   skips questions and "missing/absence" statements, and reads a founding year only from founding words.
2. Test, with the checker frozen: the first scoring reported 11 false certification claims in 7 tenders.
   Hand review found all 11 were false positives: restatements of the buyer's requirement ("le marché exige
   un hébergement ... qualifié SecNumCloud"), matrix labels, and "nous sommes en incapacité". The checker was
   then extended (a restated buyer requirement without a first-person claim is not a claim; "qualifiée" and a
   bare "titulaire" are not holding cues; "incapacité" and "unable" are negations) and the baseline was
   re-scored from the response cache. Automatic and hand-verified counts now agree (0).
3. Gold labels: award criteria whose text only points elsewhere ("See section 'Ground for decision'",
   "Please consult the procurement documents") are no longer scored for coverage (7 criteria). The
   specification texts are unchanged, so cached responses stay valid.

Every changed rule has regression tests built from the real sentences, together with true fabrications that
must still be caught ("Comme l'exige le CCTP, nous sommes certifiés SecNumCloud"). Fix 2 was informed by
test-split drafts, so M2 is scored with this frozen checker and its automatic flags are again read by hand.
