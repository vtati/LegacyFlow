#!/usr/bin/env python3
"""Seed Virginia document templates for a firm.

Usage:
    python seed_va_templates.py --firm-id <UUID> --user-id <UUID> [--db-url <URL>]

Reads template files from services/api/content-packs/templates/us-va/v0/
and inserts them into the document_templates table for the specified firm.

Templates are attorney-authored; the AI only fills {{placeholders}} at render time.
All generated documents carry the DRAFT watermark until attorney-approved.
"""

import argparse
import sys
from pathlib import Path
from uuid import UUID

import psycopg


TEMPLATES = [
    {
        "file": "last-will-and-testament.txt",
        "name": "Last Will and Testament (Virginia)",
        "template_type": "will",
        "pack_id": "us-va",
    },
    {
        "file": "durable-power-of-attorney.txt",
        "name": "Durable Financial Power of Attorney (Virginia)",
        "template_type": "poa",
        "pack_id": "us-va",
    },
    {
        "file": "advance-medical-directive.txt",
        "name": "Advance Medical Directive (Virginia)",
        "template_type": "poa",
        "pack_id": "us-va",
    },
    {
        "file": "guardianship-designation.txt",
        "name": "Designation of Guardian for Minor Children (Virginia)",
        "template_type": "instruction_letter",
        "pack_id": "us-va",
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed VA document templates")
    parser.add_argument("--firm-id", required=True, help="Firm UUID")
    parser.add_argument("--user-id", required=True, help="Creating user UUID (firm_admin)")
    parser.add_argument("--db-url", help="Postgres URL (or set DATABASE_URL env)")
    args = parser.parse_args()

    import os
    db_url = args.db_url or os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: --db-url or DATABASE_URL required", file=sys.stderr)
        return 1

    firm_id = UUID(args.firm_id)
    user_id = UUID(args.user_id)
    template_dir = Path(__file__).parent / "content-packs" / "templates" / "us-va" / "v0"

    conn = psycopg.connect(db_url)
    try:
        with conn.cursor() as cur:
            for t in TEMPLATES:
                path = template_dir / t["file"]
                if not path.exists():
                    print(f"SKIP: {t['file']} not found", file=sys.stderr)
                    continue
                body = path.read_text(encoding="utf-8")
                # Check if already exists (idempotent)
                cur.execute(
                    "SELECT id FROM document_templates WHERE firm_id = %s AND name = %s",
                    (str(firm_id), t["name"]),
                )
                if cur.fetchone():
                    print(f"EXISTS: {t['name']}")
                    continue
                cur.execute(
                    """
                    INSERT INTO document_templates
                      (firm_id, name, template_type, pack_id, version, content, created_by_user_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        str(firm_id),
                        t["name"],
                        t["template_type"],
                        t["pack_id"],
                        "1.0.0",
                        psycopg.types.json.Json({"body": body}),
                        str(user_id),
                    ),
                )
                row = cur.fetchone()
                print(f"CREATED: {t['name']} ({row['id']})")
        conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
