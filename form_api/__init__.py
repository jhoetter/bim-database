"""Customer-submission FastAPI app.

Runs as a SEPARATE process from the developer-facing api/ server. Has
its own auth (API key + per-IP rate limit), its own host/port, and
writes ONLY into the quarantine area `data/pdfs/submissions/<id>/`.
Promotion into `data/pdfs/incoming/` is a developer-side review step
mounted on the existing api/ server.
"""
