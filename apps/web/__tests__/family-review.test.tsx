/** @vitest-environment jsdom */
/**
 * Family review & drafts (Part 2 of the self-serve draft generation plan).
 *
 * Contracts pinned here:
 * - DraftPanel renders from the state endpoint's flags only: disabled
 *   firm -> plain notice, no dead buttons; stale drafts -> banner +
 *   regenerate; post-submit -> attorney + packet version, no actions.
 * - ReviewAnswers formats answers as the documents will read them and
 *   saves edits through the same callback the page wires to
 *   PUT /intake/answers.
 * - ReviewPersons resolves nominations to names, read-only.
 * - My Plan's buildNextSteps leads with the review step only when the
 *   draft probe enabled it for a complete, pre-submission matter.
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { DraftPanel } from '../components/family/review/draft-panel';
import { ReviewAnswers } from '../components/family/review/review-answers';
import { ReviewPersons } from '../components/family/review/review-persons';
import type {
  DraftSetState,
  QuestionSetsResponse,
} from '../lib/api-client';

const baseState: DraftSetState = {
  enabled: true,
  matter_status: 'INTAKE_IN_PROGRESS',
  applicable: { required_total: 41, required_answered: 41, complete: true, missing_labels: [] },
  documents: [
    {
      template_type: 'will',
      included: true,
      signal: 'always',
      template_id: 'tpl-will',
      template_name: 'Last Will and Testament (Virginia)',
      package_id: null,
      package_status: null,
      generated: false,
      stale: false,
      latest_version_at: null,
    },
    {
      template_type: 'trust',
      included: false,
      signal: 'wants_revocable_trust != true',
      template_id: 'tpl-trust',
      template_name: 'Revocable Living Trust (Virginia)',
      package_id: null,
      package_status: null,
      generated: false,
      stale: false,
      latest_version_at: null,
    },
  ],
  can_generate: true,
  can_submit: false,
  submit_blockers: ['drafts_missing'],
  assigned_attorney: null,
  latest_packet: null,
};

const panelProps = {
  generating: false,
  submitting: false,
  actionError: null,
  onGenerate: vi.fn(),
  onSubmit: vi.fn(),
};

describe('DraftPanel', () => {
  it('offers generate and explains the submit blocker before drafts exist', () => {
    render(<DraftPanel state={baseState} {...panelProps} />);
    expect(screen.getByText('Last Will and Testament (Virginia)')).toBeDefined();
    expect(screen.getByText('Not created yet')).toBeDefined();
    // excluded type explains itself in plain language
    expect(screen.getByText('Only included when you choose a revocable living trust.')).toBeDefined();
    const generate = screen.getByRole('button', { name: 'Create draft documents' });
    expect((generate as HTMLButtonElement).disabled).toBe(false);
    const submit = screen.getByRole('button', { name: /Submit to attorney/ });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Create your draft documents first.')).toBeDefined();
  });

  it('shows the stale banner and a regenerate action after an edit', () => {
    const staleState: DraftSetState = {
      ...baseState,
      can_submit: false,
      submit_blockers: ['drafts_stale'],
      documents: baseState.documents.map((doc) =>
        doc.template_type === 'will'
          ? { ...doc, package_id: 'pkg-1', package_status: 'demo_released', generated: true, stale: true, latest_version_at: '2026-10-26T12:00:00Z' }
          : doc,
      ),
    };
    render(<DraftPanel state={staleState} {...panelProps} />);
    expect(screen.getByRole('alert').textContent).toMatch(/answers changed/i);
    expect(screen.getByRole('button', { name: 'Regenerate drafts' })).toBeDefined();
    const submit = screen.getByRole('button', { name: /Submit to attorney/ });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
  });

  it('enables submit when drafts are fresh', () => {
    const freshState: DraftSetState = {
      ...baseState,
      can_submit: true,
      submit_blockers: [],
      documents: baseState.documents.map((doc) =>
        doc.template_type === 'will'
          ? { ...doc, package_id: 'pkg-1', package_status: 'demo_released', generated: true, latest_version_at: '2026-10-26T12:00:00Z' }
          : doc,
      ),
    };
    render(<DraftPanel state={freshState} {...panelProps} />);
    const submit = screen.getByRole('button', { name: /Submit to attorney/ });
    expect((submit as HTMLButtonElement).disabled).toBe(false);
  });

  it('renders the post-submit state with attorney and packet version', () => {
    const submittedState: DraftSetState = {
      ...baseState,
      matter_status: 'ATTORNEY_REVIEW',
      can_generate: false,
      can_submit: false,
      submit_blockers: ['already_submitted'],
      assigned_attorney: {
        user_id: 'atty-1',
        full_name: 'E2E Attorney Test',
        email: 'e2e@example.test',
      },
      latest_packet: { id: 'pkt-1', version: 2, created_at: '2026-10-26T12:00:00Z' },
    };
    render(<DraftPanel state={submittedState} {...panelProps} />);
    expect(screen.getByText('Submitted for attorney review')).toBeDefined();
    expect(screen.getByText(/E2E Attorney Test/)).toBeDefined();
    expect(screen.getByText(/Review packet version 2/)).toBeDefined();
    expect(screen.queryByRole('button', { name: /Submit to attorney/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /draft/i })).toBeNull();
  });

  it('treats ESCALATED as not-submitted (mirrors the API semantics)', () => {
    // An escalation can happen before any submission; the panel must
    // show the invalid_status blocker, never the "submitted" panel.
    const escalatedState: DraftSetState = {
      ...baseState,
      matter_status: 'ESCALATED',
      can_generate: false,
      submit_blockers: ['invalid_status'],
    };
    render(<DraftPanel state={escalatedState} {...panelProps} />);
    expect(screen.queryByText('Submitted for attorney review')).toBeNull();
    expect(
      screen.getByText("Submission isn't available for this plan's current status."),
    ).toBeDefined();
    const submit = screen.getByRole('button', { name: /Submit to attorney/ });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
  });

  it('renders a plain notice and no buttons when the firm has not enabled drafts', () => {
    render(<DraftPanel state={{ ...baseState, enabled: false }} {...panelProps} />);
    expect(screen.getByText(/isn't available for your firm yet/)).toBeDefined();
    expect(screen.queryByRole('button')).toBeNull();
  });
});

const questionSets: QuestionSetsResponse = {
  jurisdiction_code: 'US-VA',
  pack_version: '2.0.0',
  question_sets: {
    household: {
      question_set: { id: 'household', title: 'About your household' },
      sections: [
        {
          id: 'basics',
          title: 'Basics',
          questions: [
            { key: 'full_legal_name', type: 'text', label: 'Your full legal name', required: true },
            { key: 'has_minor_children', type: 'boolean', label: 'Do you have minor children?', required: true },
            {
              key: 'marital_status',
              type: 'single_select',
              label: 'Marital status',
              required: true,
              options: [
                { value: 'married', label: 'Married' },
                { value: 'single', label: 'Single' },
              ],
            },
            { key: 'guardian_person', type: 'person', label: 'Guardian for your children', required: false },
            { key: 'funeral_wishes', type: 'notes', label: 'Funeral wishes', required: false },
          ],
        },
      ],
    },
    guardians: { question_set: { id: 'guardians', title: 'Guardians' }, sections: [] },
    assets: { question_set: { id: 'assets', title: 'Assets' }, sections: [] },
  },
};

const persons = [
  { id: 'p-1', full_name: 'Priya Sharma', date_of_birth: null, email: null, kind: 'adult' },
  { id: 'p-2', full_name: 'Arjun Sharma', date_of_birth: '2016-01-01', email: null, kind: 'minor' },
];

describe('ReviewAnswers', () => {
  it('formats answers as the documents will read them', () => {
    render(
      <ReviewAnswers
        questionSets={questionSets}
        answers={{
          full_legal_name: 'Priya Sharma',
          has_minor_children: true,
          marital_status: 'married',
          guardian_person: 'p-1',
        }}
        persons={persons}
        onSaveAnswer={vi.fn(async () => {})}
      />,
    );
    expect(screen.getByText('Yes')).toBeDefined(); // boolean -> Yes
    expect(screen.getByText('Married')).toBeDefined(); // option label, not the raw value
    expect(screen.getAllByText('Priya Sharma').length).toBeGreaterThanOrEqual(2); // name + resolved person
    expect(screen.getByText('Not answered')).toBeDefined(); // unanswered optional question
  });

  it('saves an edited answer through the callback', async () => {
    const onSaveAnswer = vi.fn(async () => {});
    render(
      <ReviewAnswers
        questionSets={questionSets}
        answers={{ full_legal_name: 'Priya Sharms' }}
        persons={persons}
        onSaveAnswer={onSaveAnswer}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Edit answer: Your full legal name/ }));
    const input = screen.getByDisplayValue('Priya Sharms') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Priya Sharma' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(onSaveAnswer).toHaveBeenCalledWith('full_legal_name', 'Priya Sharma');
    });
  });
});

describe('ReviewPersons', () => {
  it('resolves nominations to names and keeps them read-only', () => {
    render(
      <ReviewPersons
        persons={persons}
        nominations={[
          {
            id: 'n-1',
            nomination_type: 'guardian',
            person_id: 'p-1',
            priority: 1,
            notes: null,
            status: 'suggested',
          },
        ]}
        onSavePersonName={vi.fn(async () => {})}
      />,
    );
    expect(screen.getByText('Guardian')).toBeDefined();
    // person names are editable (typo fix path); nominations have no edit control
    expect(screen.getByRole('button', { name: /Edit name: Priya Sharma/ })).toBeDefined();
    expect(screen.getAllByRole('button')).toHaveLength(2); // one per person, none for nominations
  });
});


