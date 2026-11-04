"""Golden-dataset integrity: every case parses, ids are unique, and the
schema contract from evals/README.md holds. Pure file checks — no DB,
no network — so this runs anywhere, including CI."""
import json
from pathlib import Path

GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "golden"
SUITES = {
    "extraction-paragraphs": "ext-",
    "conversations-e2e": "e2e-",
    "consistency": "con-",
    "attorney-review": "att-",
    "vault-analyst": "vlt-",
    "stt": "stt-",
}


def _load(suite):
    path = GOLDEN / f"{suite}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_all_suites_present_and_nonempty():
    for suite in SUITES:
        cases = _load(suite)
        assert len(cases) >= 2, f"{suite} has {len(cases)} cases"


def test_case_schema_and_unique_ids():
    seen = set()
    for suite, prefix in SUITES.items():
        for case in _load(suite):
            assert case["id"].startswith(prefix), case["id"]
            assert case["id"] not in seen, f"duplicate id {case['id']}"
            seen.add(case["id"])
            assert case["suite"] == suite
            assert isinstance(case["input"], dict) and case["input"]
            assert isinstance(case["gold"], dict) and case["gold"]
            assert case["notes"].strip(), f"{case['id']} needs provenance notes"


def test_extraction_cases_use_pack_keys_or_grounded_schema():
    pack_keys = set(
        (Path(__file__).resolve().parents[1] / "evals" / "pack_v2_question_keys.txt")
        .read_text().split()
    ) if (Path(__file__).resolve().parents[1] / "evals" / "pack_v2_question_keys.txt").exists() else None
    # The two grounded spot-check cases use the spot-check's 4-field schema
    # (executor_name et al.); authored cases use pack v2 keys. Both are
    # legitimate — this test only fails on keys in NEITHER vocabulary.
    grounded = {"marital_status", "primary_residence_state", "has_minor_children", "executor_name"}
    allowed = grounded | (pack_keys or set())
    for case in _load("extraction-paragraphs"):
        for key in case["input"]["question_keys"]:
            assert key in allowed, f"{case['id']}: unknown question key {key}"
