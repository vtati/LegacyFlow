/** @vitest-environment jsdom */
/**
 * Intake wizard — "Check my answers" verdict + read-only states
 * (2026-10-31, Tier 1 item 3).
 *
 * The check used to run escalation triage and report only its rule
 * outcomes, leaving the family unable to tell whether they were
 * actually complete — and a blocking outcome moved the matter to
 * ESCALATED, after which the wizard kept presenting editable fields
 * whose saves failed with a misleading "check your connection". The
 * verdict now leads with completeness (server partition) + the
 * planned document set, phrases attorney items in family language,
 * and the wizard goes honestly read-only when the matter leaves the
 * editable statuses.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

const setProgress = {
  total_questions: 3,
  answered: 1,
  required_total: 3,
  required_answered: 1,
  percent_complete: 33.33,
};

const matterState: { status: string } = { status: 'intake_in_progress' };
const evaluateResult: { current: unknown } = { current: null };

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  api: {
    listMatters: vi.fn(async () => ({ items: [] })),
    getMatter: vi.fn(async () => ({
      matter: {
        id: 'matter-1',
        title: 'Sharma plan',
        status: matterState.status,
        jurisdiction_code: 'US-VA',
        completeness_pct: 33,
        household: { id: 'hh-1', name: 'Sharma household' },
      },
    })),
    getQuestionSets: vi.fn(async () => ({
      question_sets: {
        household: {
          question_set: { id: 'household', title: 'Household profile' },
          sections: [
            {
              id: 'basics',
              title: 'About your household',
              questions: [
                { key: 'full_name', type: 'text', label: 'Full legal name?', required: true },
                { key: 'marital_status', type: 'text', label: 'Marital status?', required: true },
              ],
            },
            {
              id: 'triage',
              title: 'Quick check',
              questions: [
                { key: 'special_needs', type: 'text', label: 'Special needs?', required: true },
              ],
            },
          ],
        },
      },
    })),
    getAnswers: vi.fn(async () => ({ answers: { full_name: 'Raj Sharma' } })),
    getHousehold: vi.fn(async () => ({ persons: [] })),
    getIntakeProgress: vi.fn(async () => ({
      sets: { household: setProgress },
      sections: {
        household: {
          basics: { ...setProgress, total_questions: 2, answered: 1, percent_complete: 50 },
          triage: { ...setProgress, total_questions: 1, answered: 0, percent_complete: 0 },
        },
      },
      overall: setProgress,
      completeness_pct: 33.33,
      documents: [
        { template_type: 'will', label: 'Last Will and Testament', included: true, signal: 'always' },
        {
          template_type: 'guardianship',
          label: 'Guardianship Designation',
          included: true,
          signal: 'has_minor_children == true',
        },
        {
          template_type: 'trust',
          label: 'Revocable Living Trust',
          included: false,
          signal: 'wants_revocable_trust != true',
        },
      ],
    })),
    putAnswers: vi.fn(async () => ({ answers: {}, updated_at: null })),
    evaluateIntake: vi.fn(async () => evaluateResult.current),
    submitMatter: vi.fn(async () => ({})),
    createPerson: vi.fn(async () => ({
      id: 'person-new',
      full_name: 'New Person',
      date_of_birth: null,
      email: null,
      kind: 'adult',
    })),
  },
}));

import { IntakeWizard } from '../components/family/intake/intake-wizard';

const COMPLETE_EVALUATION = {
  triggered: [],
  has_blocking: false,
  checked_at: '2026-10-31T13:50:00Z',
  completeness: { required_total: 3, required_answered: 3, missing: [] },
  documents: [
    { template_type: 'will', label: 'Last Will and Testament', included: true, signal: 'always' },
  ],
};

describe('IntakeWizard check panel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    matterState.status = 'intake_in_progress';
    evaluateResult.current = COMPLETE_EVALUATION;
  });

  it('shows the planned document set from the progress payload', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    expect(
      await screen.findByText(/Based on your answers so far, your plan will produce:/),
    ).toBeDefined();
    expect(screen.getByText(/Last Will and Testament · Guardianship Designation/)).toBeDefined();
    // Excluded documents are never listed.
    expect(screen.queryByText(/Revocable Living Trust/)).toBeNull();
  });

  it('verdict leads with completeness and missing entries navigate to their section', async () => {
    evaluateResult.current = {
      ...COMPLETE_EVALUATION,
      completeness: {
        required_total: 3,
        required_answered: 2,
        missing: [
          {
            key: 'special_needs',
            label: 'Special needs?',
            section_title: 'Quick check',
            set_id: 'household',
            section_id: 'triage',
          },
        ],
      },
    };
    render(<IntakeWizard matterId="matter-1" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Check my answers' }));
    expect(
      await screen.findByText(/You've answered 2 of 3 required questions/),
    ).toBeDefined();
    const jump = screen.getByRole('button', { name: /Special needs\?/ });
    fireEvent.click(jump);
    // The wizard switched to the Quick check section.
    expect(
      await screen.findByRole('heading', { name: 'Quick check' }),
    ).toBeDefined();
  });

  it('a complete check says so plainly, with no jargon', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Check my answers' }));
    expect(await screen.findByText(/All 3 required answers are in/)).toBeDefined();
    expect(screen.getByText(/Nothing needs your attorney's attention right now/)).toBeDefined();
    expect(document.body.textContent?.toLowerCase()).not.toContain('escalat');
  });

  it('a blocking check engages the read-only presentation immediately', async () => {
    evaluateResult.current = {
      ...COMPLETE_EVALUATION,
      has_blocking: true,
      triggered: [
        {
          rule_id: 'VA-ESC-TEST',
          severity: 'blocking',
          outcome: 'Attorney review',
          user_message: 'Your attorney needs to review this answer.',
        },
      ],
    };
    render(<IntakeWizard matterId="matter-1" />);
    const nameField = (await screen.findByLabelText(/Full legal name/)) as HTMLInputElement;
    expect(nameField.disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Check my answers' }));
    expect(await screen.findByText('Attorney review required')).toBeDefined();
    expect(
      screen.getByText('Your plan is with your attorney for a closer look'),
    ).toBeDefined();
    expect(nameField.disabled).toBe(true);
    expect(
      (screen.getByRole('button', { name: 'Check my answers' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(document.body.textContent?.toLowerCase()).not.toContain('escalated');
    expect(screen.queryByText(/check your connection/)).toBeNull();
  });

  it('a matter that loads in ESCALATED state is read-only with a family-readable status', async () => {
    matterState.status = 'ESCALATED';
    render(<IntakeWizard matterId="matter-1" />);
    expect(
      await screen.findByText('Your plan is with your attorney for a closer look'),
    ).toBeDefined();
    expect(screen.getByText('With your attorney')).toBeDefined();
    expect(screen.queryByText('Escalated')).toBeNull();
    const nameField = (await screen.findByLabelText(/Full legal name/)) as HTMLInputElement;
    expect(nameField.disabled).toBe(true);
    expect(
      (screen.getByRole('button', { name: 'Check my answers' }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
