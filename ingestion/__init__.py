"""Source-agnostic ingestion + preprocessing pipeline.

Both the developer batch CLI (`python -m ingestion.cli`) and the customer
submission form (`form_api/main.py`) route through this package. The output
is the canonical R1 intake bundle shape under
`data/pdfs/incoming/<house_key>/`:

    manifest.json          ← intake_manifest.schema.json (v2.0)
    <house_key>.pdf        ← consolidated, rectified PDF
    source/                ← preserved originals (dedup'd by SHA-256)

Downstream R2 scene extraction reads the consolidated PDF unchanged; the
rectification + restoration happens HERE so the corpus already contains
raw↔rectified pairs (raw lives under source/, rectified is the PDF).
"""

VERSION = "2.0.0"
"""Pipeline version. Written into every manifest at write time."""
