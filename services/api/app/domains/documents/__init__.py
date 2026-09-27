"""Documents domain: templates, packages, demo generation, signing checklists,
funding tasks, vault shares, instruction letters, and reminders (Slice 4).

Demo documents are ALWAYS watermarked "DEMO ONLY — NOT LEGALLY VALID" and
are never represented as legally valid. Production release fails closed
(``release_production`` always raises ``ProductionReleaseUnavailableError``,
HTTP 409); real release requires Slice 5+ attorney gates.
"""
