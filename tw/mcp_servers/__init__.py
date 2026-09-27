"""MCP servers written for this project. Each exposes the minimum an agent needs:

- firm_kb     read-only   the fictional firm's references, consultants, certifications, methods
- tenders     read-only   tender frames and specifications (untrusted third-party text)
- compliance  read-only   Project A's GDPR / AI Act copilot, over HTTP
- outbox      write       submit a response; refused without a human approval token

Run one over stdio with, e.g., `python -m tw.mcp_servers.firm_kb`.
"""
