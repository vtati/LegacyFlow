# LegacyFlow eval suites — golden dataset

Versioned golden data for the AI eval pipeline (plan:
`~/workspace/goals/legacyflow-product-build/files/ai-eval-pipeline-plan-2026-11-04.md`).
Cases live in `golden/<suite>.jsonl`, one JSON object per line, and are
mirrored to LangSmith datasets in the `legacyflow-evals` project so runs
appear as comparable experiments. **All cases are fictional.** Production
data is never used here — the Sharma family and invented variants only.

## Case schema (common)

```json
{
  "id": "ext-001",
  "suite": "extraction-paragraphs",
  "tags": ["negation", "hard-class"],
  "input": { },
  "gold": { },
  "notes": "Why this case exists; what failure it guards."
}
```

- `id` is stable forever: `<suite-prefix>-<nnn>`. Never renumber; a
  retired case is removed and its id is not reused.
- `gold` is the ground truth a run is scored against. Gold labels are
  reviewed like code — a wrong gold label is a bug in the dataset.
- Every field-level gold entry carries **source attribution**: which
  input turn (or document span) the fact came from. Wrong-source scoring
  compares attribution, never raw string equality (Oct 2026 lesson:
  paraphrases are not wrong-source errors).

## Suites

### extraction-paragraphs (`ext-`)
Input: one user paragraph (recorded-answer style) plus the question it
answers. Gold: the fields the extractor must propose from it, each with
`key`, `value`, `source` (`stated` | `inferred-confirm` | `none`), and
the fields it must NOT propose (`must_not_propose`). Metrics: field
precision/recall, wrong-source count (hard gate: 0), normalization
accuracy.

### conversations-e2e (`e2e-`)
Input: a persona script (ordered user messages, cooperative-user style
from the Oct 2026 A/B harness) plus persona facts. Gold: the expected
final profile (field → value), the escalations that must fire, the
document set generation must produce, max user turns, and
`repeated_questions: 0`. Metrics: profile diff vs gold, escalation
precision/recall, document-set equality (hard gate), repeats, turns.

### consistency (`con-`)
Input: a verified-facts list (key, value, provenance) as the reviewer
receives it. Gold: the contradictions present (pairs + expected
severity) — empty for clean controls. Metrics: catch rate (hard gate:
no missed `blocking`), false positives on clean cases, severity
accuracy.

### attorney-review (`att-`)
Input: a document-package snapshot in the reviewer's source shape.
Gold: the findings that must be raised (check class / model finding,
severity, question-form suggested fix) — empty for clean packets.
Metrics: catch per seeded class (hard gate for blocking classes),
false findings on clean packets, suggested-fix form compliance.

### vault-analyst (`vlt-`)
Input: document text + filename, as the analyst receives it. Gold:
expected classification and key fields. Metrics: classification
accuracy, field accuracy.

### stt (`stt-`)
Input: an audio clip reference (clips live outside git; the case holds
the filename and recording script). Gold: the transcript plus the
critical tokens (names, numbers, dates) that must survive. Metrics:
word error rate, critical-token accuracy. This suite doubles as the
whisper-1 migration benchmark (removal deadline 2027-02-26).

## Adding a case

1. Write the case with gold labels and a `notes` line naming the
   failure it guards (a real incident, a benchmark miss, or a named
   edge class). Cases without a guarded failure do not belong here.
2. Run the suite locally; the new case must score deterministically
   against its gold on the deterministic evaluators.
3. Mirror to LangSmith so experiments stay comparable.

(Steps 2–3 use `run_evals.py`, which lands with the evaluator phase of
the pipeline — until then this README is the schema contract the
runner is built against.)

## LangSmith mirror (2026-11-04)

The JSONL files above are the source of truth; each suite is mirrored to a
LangSmith dataset for experiment runs and Demo Day display:

| Suite | LangSmith dataset | Examples |
|---|---|---|
| extraction-paragraphs | legacyflow-extraction-paragraphs-v1 (`41580ad4`) | 12 |
| conversations-e2e | legacyflow-conversations-e2e-v1 (`bf12656e`, re-mirrored 2026-11-04 after the F-1 dataset update; original mirror `b3fe1d0d` deleted) | 2 |
| consistency | legacyflow-consistency-v1 (`c5a882ea`) | 12 |
| attorney-review | legacyflow-attorney-review-v1 (`bb9996f9`) | 7 |
| vault-analyst | legacyflow-vault-analyst-v1 (`04781251`) | 8 |
| stt | legacyflow-stt-v1 (`989b5dfc`) | 5 |

Upload notes (learned the hard way): create examples one by one via
`POST /api/v1/examples` — the bulk endpoint silently no-ops with list-valued
metadata; the connector helper's CLI truncates printed responses at 4000
chars, so verify counts via `GET /api/v1/datasets/{id}` (`example_count`),
never by parsing create echoes. The examples LIST endpoint currently
400s on this workspace ("as_of" quirk) — dedupe by recreating the dataset
if counts ever drift.

## Running the evals (Phase 3)

```
cd services/api
OPENAI_API_KEY=... .venv/bin/python -m evals.run_evals \
  --suite extraction-paragraphs --suite consistency --suite vault-analyst \
  --out evals/runs/<run-name>
```

- In-process legs (extraction, consistency, vault) call the production
  modules' own model functions with the production prompts.
- `--suite stt` synthesizes each script via TTS (cached in `evals/audio/`)
  and transcribes with the production Whisper provider.
- `--suite conversations-e2e` runs against the live API with the
  recording test account: set `EVAL_ACCOUNT_PASSWORD`,
  `SUPABASE_ANON_KEY`, and `EVAL_HOUSEHOLD_ID=05ae3b30-ea55-4027-b118-4833e80d1c83` (the canonical Sharma recording household). Throwaway matters are deleted afterwards unless
  `--keep-matters` is passed.
- `--suite attorney-review` is intentionally not wired here: it needs a
  frozen packet + snapshot mutation (Oct 27 `verify_seeded.py`
  mechanics) and lands with the baseline phase. Its evaluator is
  unit-tested (`tests/test_evaluators.py`).
- Every run writes `results.json` (per-case verdicts + raw outputs),
  `scorecard.json`, and a self-contained `scorecard.html`.
- `--dry-run` loads all cases and exercises the wiring without any
  model calls.
