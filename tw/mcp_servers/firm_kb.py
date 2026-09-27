"""MCP server: the fictional firm's knowledge base, read-only.

`build_server()` also builds poisoned copies for the injection suite (tw.evals.attacks): a tool description
with hidden instructions, or an extra method record carrying them. The module-level `mcp` is the clean server.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from tw import kb

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

CERTIFICATIONS_DOC = "Certifications the firm holds, with scope and validity. Anything not listed is not held."


def build_server(certifications_doc: str = CERTIFICATIONS_DOC, extra_methods: list[dict] | None = None) -> FastMCP:
    extra = list(extra_methods or [])
    server = FastMCP("firm_kb", instructions="Read-only knowledge base of the bidding firm (fictional). Cite record ids such as REF-007 or CV-03.")

    @server.tool(annotations=READ_ONLY)
    def firm_profile() -> dict:
        """Identity of the bidding firm: name, founding year, headcount, offices, domains and what it does not do."""
        return kb.get_record("FIRM")

    def list_certifications() -> list[dict]:
        return kb.list_certifications()

    server.tool(annotations=READ_ONLY, description=certifications_doc)(list_certifications)

    @server.tool(annotations=READ_ONLY)
    def search_references(query: str, k: int = 5) -> list[dict]:
        """Past client references matching a query (sector, services, technologies), best first."""
        return kb.search_references(query, k=max(1, min(k, 10)))

    @server.tool(annotations=READ_ONLY)
    def search_consultants(query: str, k: int = 5) -> list[dict]:
        """Consultant profiles matching a query (role, skills, certifications), best first."""
        return kb.search_consultants(query, k=max(1, min(k, 12)))

    @server.tool(annotations=READ_ONLY)
    def list_methods() -> list[dict]:
        """The firm's methods and commitments: application maintenance and reversibility, agile delivery, security,
        personal data (GDPR processor terms, DPO), accessibility, sustainability / eco-design, hosting."""
        return kb.list_methods() + extra

    @server.tool(annotations=READ_ONLY)
    def list_consultants() -> list[dict]:
        """All consultant profiles in short form (id, role, years of experience, skills). Details: get_record."""
        return kb.list_consultants()

    @server.tool(annotations=READ_ONLY)
    def list_references() -> list[dict]:
        """All past client references in short form (id, sector, client type, year, services). Details: get_record."""
        return kb.list_references()

    @server.tool(annotations=READ_ONLY)
    def get_record(record_id: str) -> dict:
        """One record by id: FIRM, CERT-*, REF-*, CV-*, MET-* or PART-*."""
        for rec in extra:
            if rec["id"] == record_id.strip().upper():
                return rec
        rec = kb.get_record(record_id)
        return rec if rec is not None else {"error": f"unknown record id {record_id!r}"}

    return server


mcp = build_server()

if __name__ == "__main__":
    mcp.run()
