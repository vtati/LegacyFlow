"""Regenerate applies-if-parity.json — the cross-language parity fixture.

The fixture pins the TypeScript port of the applies_if evaluator
(apps/web/lib/applies-if.ts) against the server engine
(services/api/app/domains/intake/question_sets.py): every expression
below (the real v2 pack gates plus Kleene-logic edge cases) crossed
with every answer set, with `expected` computed BY THE PYTHON
EVALUATOR. If the engines ever drift, __tests__/applies-if.test.ts
fails.

Run from the repo root (needs the API's dependencies importable —
the repo venv works):

    services/api/.venv/bin/python apps/web/__tests__/fixtures/generate_parity_fixture.py

It rewrites applies-if-parity.json next to this script. Commit the
regenerated fixture together with any evaluator change.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "services" / "api"))

from app.domains.intake.question_sets import evaluate_applies_if  # noqa: E402

EXPRESSIONS = [
    "has_specific_bequests == true",
    "has_minor_children == true",
    "wants_poa_document != false",
    "wants_healthcare_directive != false",
    "wants_revocable_trust == true",
    "marital_status == 'married' or marital_status == 'partnered'",
    "marital_status == 'married'",
    "a == true and b == true",
    "a == true or b == true",
    "not a == true",
    "not (a == true or b == false)",
    "(a == true or b == true) and c != 'x'",
    "count == 2",
    "count == true",
    "count != true",
    "name == 'x'",
    "name != 'x'",
    "flag",
    "not flag",
    "a == true and b",
    "a == b",
    "a != b",
    "count == 2.5",
    "count == -3",
    "name == 'a\\'b'",
]

ANSWER_SETS: list[dict] = [
    {},
    {"has_specific_bequests": True},
    {"has_specific_bequests": False},
    {"has_minor_children": False},
    {"wants_poa_document": False},
    {"wants_poa_document": True},
    {"marital_status": "married"},
    {"marital_status": "single"},
    {"marital_status": ""},
    {"a": True, "b": True, "c": "y"},
    {"a": True, "b": False, "c": "x"},
    {"a": False},
    {"b": True},
    {"count": 2},
    {"count": 1},
    {"count": True},
    {"name": "x"},
    {"name": "z"},
    {"flag": True},
    {"flag": False},
    {"flag": 0},
    {"a": [], "b": {}},
    {"count": 2.5},
    {"count": -3},
    {"name": "a'b"},
    {"a": [1], "b": {"0": 1}},
    {"a": [True], "b": [1]},
]


def main() -> None:
    cases = []
    for expr in EXPRESSIONS:
        for answers in ANSWER_SETS:
            cases.append(
                {
                    "expr": expr,
                    "answers": answers,
                    "expected": evaluate_applies_if(expr, answers),
                }
            )
    out = Path(__file__).with_name("applies-if-parity.json")
    out.write_text(json.dumps(cases) + "\n")
    print(f"wrote {len(cases)} cases to {out}")


if __name__ == "__main__":
    main()
