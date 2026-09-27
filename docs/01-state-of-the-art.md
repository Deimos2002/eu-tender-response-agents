# 01 — State of the art and prior art (2026-09-26)

Question: what would a multi-agent tender-response writer add over what already exists, and what can be
measured honestly on free LLM tiers?

## 1. Existing tender / RFP agents (portfolio level)

Multi-agent RFP assistants built on LangGraph are already a common portfolio project:

- [agentic-rfp-system](https://github.com/TallaSatyaGanesh/agentic-rfp-system): 6-agent LangGraph workflow,
  ChromaDB grounding, red-team critique loop, source traceability, two human-in-the-loop gates.
- [MultiAgent-RFP](https://github.com/Nehan757/MultiAgent-RFP): request classification, RFP generation,
  approval validation, supplier communication.
- [proposalcraft-agent](https://github.com/Deeps72-ux/proposalcraft-agent): planning, drafting, pricing,
  PDF/PPTX/DOCX export.

**Implication:** planner + writer + reviewer + approval gate is table stakes. None of these publishes a
measured evaluation (requirement coverage, fabricated capabilities, injection robustness, cost per case).

## 2. Evaluation of generated proposals

No benchmark for tender-response quality was found. Adjacent work: hallucination benchmarks
([HalluLens](https://arxiv.org/abs/2504.17550), ACL 2025), LLMs for bid compliance checking in
government procurement ([Springer 2024](https://link.springer.com/chapter/10.1007/978-3-031-68211-7_3)),
tender-document *generation* on the buyer side
([Progress in AI 2026](https://link.springer.com/article/10.1007/s13748-026-00457-5)).

**Implication:** the metrics must be defined here. Lesson from Project A: prefer deterministic checks
(coverage mapping, claims verified against a structured knowledge base) and treat LLM-judge scores as
secondary, cross-checked by a second judge.

## 3. Security of tool-using agents and MCP

- Tool poisoning (instructions hidden in MCP tool descriptions or tool outputs) is benchmarked: MCPTox
  (AAAI 2026), MCP Security Bench and MCP-SafetyBench (ICLR 2026). Reported attack success: 36.5% on
  average over 45 live servers and 20 models, above 60% in other studies
  ([CSA note](https://labs.cloudsecurityalliance.org/research/csa-research-note-mcp-tool-poisoning-auto-execution-20260701/),
  [MCP Pitfall Lab](https://arxiv.org/pdf/2604.21477)).
- Defences are architectural: action-selector, plan-then-execute, dual-LLM, context minimisation
  ([Design Patterns for Securing LLM Agents](https://arxiv.org/html/2506.08837v3)). CaMeL keeps 77% of
  AgentDojo tasks with security guarantees versus 84% undefended: security costs utility, and that
  trade-off is measurable.

**Implication:** a tender is untrusted input written by a third party. An injection suite (poisoned
tender, poisoned tool description, poisoned knowledge-base record) with attack success rate and utility
under attack, baseline vs defended, is a real differentiator.

## 4. Tooling (current)

- MCP specification rewrite of July 2026: stateless core, **elicitation** (a tool can pause to ask the
  caller), client-side caching of tool lists.
- LangChain ships MCP natively (`langchain[mcp]` ≥ 1.4, beta, built on FastMCP; OAuth 2.1, bearer and
  machine-to-machine auth; elicitation mapped to LangGraph interrupts)
  ([LangChain blog](https://www.langchain.com/blog/mcp-in-langchain-stateless-protocol-elicitation-and-more)).
  `langchain-mcp-adapters` is still maintained. Pin versions at build time; the new package is beta.

## 5. Data

- **Tenders:** TED (Tenders Electronic Daily) search API, anonymous access
  (`POST https://api.ted.europa.eu/v3/notices/search`), about 75,000 notices a month, FR/EN, structured
  eForms fields (deadlines, lots, CPV, award criteria). Full tender specifications (DCE / cahier des
  charges) usually sit on buyer platforms, not TED. Reuse terms to confirm before publishing any data.
- **Bidder side:** no real firm's references or CVs can be used. A **fictional** consulting firm with a
  synthetic, structured knowledge base (references, consultant profiles, certifications, methods) is
  required; it also makes claim verification deterministic.

## 6. Where this project can add value

1. **Measured grounding:** every statement about the bidding firm must cite a knowledge-base record,
   and a verifier checks it; planted capability gaps test whether the writer admits a gap instead of
   inventing a certification.
2. **Requirement coverage** checked against an explicit compliance matrix.
3. **Injection robustness** with baseline vs defended architectures, in CI.
4. **Does multi-agent help?** Compare against a single agent with the same tools and budget, as
   Project A compared RAG with a closed-book baseline.
5. **Cost and latency per tender** on free tiers.
6. **Composition:** Project A (GDPR / AI Act copilot) exposed as an MCP tool for the compliance sections.
