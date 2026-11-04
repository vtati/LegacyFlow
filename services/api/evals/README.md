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
