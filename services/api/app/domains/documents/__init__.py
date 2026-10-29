"""Documents domain: templates, packages, demo generation, signing checklists,
funding tasks, vault shares, instruction letters, and reminders (Slice 4).

Demo documents are ALWAYS watermarked "DEMO ONLY — NOT LEGALLY VALID" and
are never represented as legally valid. Production release is a real,
gated path: ``release_production`` renders clean final documents only
for an attorney / firm_admin actor when the matter's
``document_release`` gate has been approved; otherwise it fails closed
with HTTP 409.
"""
