/**
 * Parity tests for the client-side applies_if evaluator
 * (lib/applies-if.ts) against the server engine
 * (services/api/app/domains/intake/question_sets.py).
 *
 * The fixture's `expected` values were PRODUCED BY the Python
 * evaluator over the real pack expressions plus Kleene-logic edge
 * cases (675 cases: every expression x every answer set). If the two
 * engines ever drift, this suite fails.
 */
import { describe, expect, it } from 'vitest';
import {
  evaluateAppliesIf,
  isAnsweredValue,
  isSectionPruned,
  questionApplicability,
  visibleQuestions,
} from '../lib/applies-if';
import parityCases from './fixtures/applies-if-parity.json';

interface ParityCase {
  expr: string;
  answers: Record<string, unknown>;
  expected: boolean | null;
}

describe('evaluateAppliesIf — server parity battery', () => {
  it('matches the Python evaluator on all 675 generated cases', () => {
    const failures: string[] = [];
    for (const testCase of parityCases as ParityCase[]) {
      let actual: boolean | null;
      try {
        actual = evaluateAppliesIf(testCase.expr, testCase.answers);
      } catch (err) {
        failures.push(`${testCase.expr} / ${JSON.stringify(testCase.answers)} threw ${err}`);
        continue;
      }
      if (actual !== testCase.expected) {
        failures.push(
          `${testCase.expr} / ${JSON.stringify(testCase.answers)}: got ${actual}, want ${testCase.expected}`,
        );
      }
    }
    expect(failures).toEqual([]);
  });
});

describe('isAnsweredValue (server is_answered semantics)', () => {
  it('treats false and 0 as answers, empty values as unanswered', () => {
    expect(isAnsweredValue(false)).toBe(true);
    expect(isAnsweredValue(0)).toBe(true);
    expect(isAnsweredValue(null)).toBe(false);
    expect(isAnsweredValue(undefined)).toBe(false);
    expect(isAnsweredValue('')).toBe(false);
    expect(isAnsweredValue([])).toBe(false);
    expect(isAnsweredValue({})).toBe(false);
    expect(isAnsweredValue('x')).toBe(true);
    expect(isAnsweredValue(['x'])).toBe(true);
  });
});

describe('question applicability (the 2026-10-31 bequests defect)', () => {
  const section = { applies_if: null };
  const gate = { key: 'has_specific_bequests', applies_if: undefined };
  const followUp = {
    key: 'specific_bequests',
    applies_if: 'has_specific_bequests == true',
  };

  it('prunes the follow-up when the gate is answered No', () => {
    const answers = { has_specific_bequests: false };
    expect(questionApplicability(section, followUp, answers)).toBe('pruned');
    expect(visibleQuestions({ ...section, questions: [gate, followUp] }, answers)).toEqual([gate]);
  });

  it('keeps the follow-up while the gate is unanswered (pending, fail-closed)', () => {
    expect(questionApplicability(section, followUp, {})).toBe('pending');
    expect(visibleQuestions({ ...section, questions: [gate, followUp] }, {})).toEqual([
      gate,
      followUp,
    ]);
  });

  it('shows the follow-up when the gate is answered Yes', () => {
    const answers = { has_specific_bequests: true };
    expect(questionApplicability(section, followUp, answers)).toBe('applicable');
  });

  it('combines section-level and question-level gates (any false prunes)', () => {
    const gatedSection = { applies_if: 'wants_poa_document != false' };
    const plain = { key: 'poa_agent_person', applies_if: undefined };
    expect(questionApplicability(gatedSection, plain, { wants_poa_document: false })).toBe(
      'pruned',
    );
    expect(isSectionPruned({ ...gatedSection, questions: [plain] }, { wants_poa_document: false })).toBe(
      true,
    );
    expect(isSectionPruned({ ...gatedSection, questions: [plain] }, {})).toBe(false);
  });

  it('fails open for display on a malformed expression', () => {
    const broken = { key: 'x', applies_if: 'a ==' };
    expect(questionApplicability(section, broken, {})).toBe('applicable');
  });
});
