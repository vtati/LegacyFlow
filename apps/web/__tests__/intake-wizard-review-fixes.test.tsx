/** @vitest-environment jsdom */
/**
 * Intake wizard — review-fix regressions (2026-10-31, diff review).
 *
 * H1: Check/Submit must flush the pending autosave first — the server
 *     judges stored answers only, and a blocking verdict moves the
 *     matter out of the editable statuses, after which an unflushed
 *     edit could never be sent (while the banner claims it is saved).
 * H2: per-matter state (verdict, submitted) must not leak across
 *     matters when the wizard instance is reused.
 * M2 gaps: unmount flush, read-only flush suppression + the
 *     non-ESCALATED read-only banner, the add-person flow, and
 *     wizard-level pruned rendering.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const control = vi.hoisted(() => ({
  events: [] as string[],
  matterStatus: 'intake_in_progress',
  initialAnswers: {} as Record<string, unknown>,
  putShouldFail: false,
  failProgressAfterPut: false,
  evaluateThrows: false,
  evaluateDeferred: null as Promise<unknown> | null,
  evaluateResult: null as unknown,
  createPersonCalls: [] as Record<string, unknown>[],
  putOpts: [] as Array<{ keepalive?: boolean } | undefined>,
}));

const setProgress = {
  total_questions: 5,
  answered: 0,
  required_total: 5,
  required_answered: 0,
  percent_complete: 0,
};

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  api: {
    listMatters: vi.fn(async () => ({ items: [] })),
    getMatter: vi.fn(async () => ({
      matter: {
        id: 'matter-x',
        title: 'Plan',
        status: control.matterStatus,
        jurisdiction_code: 'US-VA',
        completeness_pct: 0,
        household: { id: 'hh-1', name: 'Household' },
      },
    })),
    getQuestionSets: vi.fn(async () => ({
      question_sets: {
        household: {
          question_set: { id: 'household', title: 'Household profile' },
          sections: [
            {
              id: 'basics',
              title: 'About you',
              questions: [
                { key: 'full_name', type: 'text', label: 'Full legal name?', required: true },
                { key: 'wants_revocable_trust', type: 'boolean', label: 'Do you want a trust?', required: false },
              ],
            },
            {
              id: 'bequests',
              title: 'Specific bequests',
              questions: [
                { key: 'has_specific_bequests', type: 'boolean', label: 'Any specific gifts?', required: false },
                {
                  key: 'specific_bequests',
                  type: 'text',
                  label: 'Describe each specific gift',
                  required: true,
                  applies_if: 'has_specific_bequests == true',
                },
              ],
            },
            {
              id: 'executors',
              title: 'Executors',
              questions: [
                { key: 'primary_executor_person', type: 'person', label: 'Who should serve as your executor?', required: true },
              ],
            },
            {
              id: 'trust',
              title: 'Trust terms',
              applies_if: 'wants_revocable_trust == true',
              questions: [
                { key: 'trustee_person', type: 'person', label: 'Who should serve as trustee?', required: true },
              ],
            },
          ],
        },
      },
    })),
    getAnswers: vi.fn(async () => ({ answers: control.initialAnswers })),
    getHousehold: vi.fn(async () => ({ persons: [] })),
    getIntakeProgress: vi.fn(async () => {
      if (control.failProgressAfterPut && control.events.includes('put')) {
        throw new Error('progress unavailable');
      }
      return {
        sets: { household: setProgress },
        sections: { household: {} },
        overall: setProgress,
        completeness_pct: 0,
        documents: [],
      };
    }),
    putAnswers: vi.fn(async (_id: string, _answers: unknown, opts?: { keepalive?: boolean }) => {
      control.events.push('put');
      control.putOpts.push(opts);
      if (control.putShouldFail) throw new Error('network down');
      return { answers: {}, updated_at: null };
    }),
    evaluateIntake: vi.fn(async () => {
      control.events.push('eval');
      if (control.evaluateThrows) throw new Error('evaluate exploded');
      if (control.evaluateDeferred) return control.evaluateDeferred;
      return control.evaluateResult;
    }),
    submitMatter: vi.fn(async () => {
      control.events.push('submit');
      return {
        id: 'matter-x',
        title: 'Plan',
        status: 'INTAKE_SUBMITTED',
        household: { id: 'hh-1', name: 'Household' },
        jurisdiction_code: 'US-VA',
      };
    }),
    createPerson: vi.fn(async (_matterId: string, input: Record<string, unknown>) => {
      control.createPersonCalls.push(input);
      return {
        id: 'person-new',
        full_name: input.full_name,
        date_of_birth: null,
        email: null,
        kind: input.kind,
      };
    }),
  },
}));

import { IntakeWizard } from '../components/family/intake/intake-wizard';
import { api } from '@/lib/api-client';

const OK_EVALUATION = {
  triggered: [],
  has_blocking: false,
  checked_at: '2026-10-31T15:00:00Z',
  completeness: { required_total: 5, required_answered: 5, missing: [] },
  documents: [{ template_type: 'will', label: 'Last Will and Testament', included: true, signal: 'always' }],
};

const BLOCKING_EVALUATION = {
  ...OK_EVALUATION,
  has_blocking: true,
  triggered: [
    { rule_id: 'VA-ESC-T', severity: 'blocking', outcome: 'Attorney review', user_message: 'Needs review.' },
  ],
};

function navToSection(title: string) {
  const buttons = screen.getAllByRole('button').filter((b) => b.textContent?.includes(title));
  fireEvent.click(buttons[0]);
}

describe('IntakeWizard review fixes', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    control.events = [];
    control.matterStatus = 'intake_in_progress';
    control.initialAnswers = {};
    control.putShouldFail = false;
    control.failProgressAfterPut = false;
    control.evaluateThrows = false;
    control.evaluateDeferred = null;
    control.evaluateResult = OK_EVALUATION;
    control.createPersonCalls = [];
    control.putOpts = [];
  });

  it('H1: Check flushes the pending edit BEFORE evaluating', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    const nameField = await screen.findByLabelText(/Full legal name/);
    fireEvent.change(nameField, { target: { value: 'Fresh Edit' } });
    // Click Check immediately — well inside the 800ms debounce.
    fireEvent.click(screen.getByRole('button', { name: 'Check my answers' }));
    expect(await screen.findByText(/All 5 required answers are in/)).toBeDefined();
    expect(control.events).toEqual(['put', 'eval']);
    const putPayload = (api.putAnswers as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(putPayload).toMatchObject({ full_name: 'Fresh Edit' });
  });

  it('H1: a failed flush aborts the check with an honest message', async () => {
    control.putShouldFail = true;
    render(<IntakeWizard matterId="matter-1" />);
    const nameField = await screen.findByLabelText(/Full legal name/);
    fireEvent.change(nameField, { target: { value: 'Unsaved Edit' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check my answers' }));
    expect(
      await screen.findByText(/couldn't be saved, so the check didn't run/),
    ).toBeDefined();
    expect(control.events).not.toContain('eval');
  });

  it('B: unmounting inside the debounce window still sends the edit', async () => {
    const { unmount } = render(<IntakeWizard matterId="matter-1" />);
    const nameField = await screen.findByLabelText(/Full legal name/);
    fireEvent.change(nameField, { target: { value: 'Last Second Edit' } });
    unmount();
    await waitFor(() => expect(api.putAnswers).toHaveBeenCalled());
    const putPayload = (api.putAnswers as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(putPayload).toMatchObject({ full_name: 'Last Second Edit' });
  });

  it('read-only (submitted) matter: banner, disabled fields, flush suppressed', async () => {
    control.matterStatus = 'INTAKE_SUBMITTED';
    const { unmount } = render(<IntakeWizard matterId="matter-1" />);
    expect(await screen.findByText('This plan has moved to the next step')).toBeDefined();
    const nameField = (await screen.findByLabelText(/Full legal name/)) as HTMLInputElement;
    expect(nameField.disabled).toBe(true);
    unmount();
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(api.putAnswers).not.toHaveBeenCalled();
  });

  it('A: add-a-person creates with an API-mintable role and selects the new person', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    await screen.findByLabelText(/Full legal name/);
    navToSection('Executors');
    fireEvent.click(await screen.findByRole('button', { name: /Add a person/ }));
    fireEvent.change(await screen.findByLabelText('Full name'), {
      target: { value: 'Added In Flow' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add and choose this person' }));
    await waitFor(() => expect(control.createPersonCalls).toHaveLength(1));
    expect(control.createPersonCalls[0]).toMatchObject({
      full_name: 'Added In Flow',
      kind: 'adult',
      relationship_role: 'trusted_contact',
    });
    const select = (await screen.findByLabelText(/Who should serve as your executor/)) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe('person-new'));
  });

  it('C: pruned questions and fully-pruned sections do not render', async () => {
    control.initialAnswers = { has_specific_bequests: false, wants_revocable_trust: false };
    render(<IntakeWizard matterId="matter-1" />);
    await screen.findByLabelText(/Full legal name/);
    navToSection('Specific bequests');
    expect(await screen.findByText(/Any specific gifts/)).toBeDefined();
    expect(screen.queryByText(/Describe each specific gift/)).toBeNull();
    const navTitles = screen
      .getAllByRole('button')
      .map((b) => b.textContent ?? '')
      .join(' | ');
    expect(navTitles).not.toContain('Trust terms');
  });

  it('H2: switching matters clears the previous verdict and its read-only lock', async () => {
    control.evaluateResult = BLOCKING_EVALUATION;
    const { rerender } = render(<IntakeWizard matterId="matter-1" />);
    await screen.findByLabelText(/Full legal name/);
    fireEvent.click(screen.getByRole('button', { name: 'Check my answers' }));
    expect(
      await screen.findByText('Your plan is with your attorney for a closer look'),
    ).toBeDefined();
    rerender(<IntakeWizard matterId="matter-2" />);
    await waitFor(() =>
      expect(screen.queryByText('Your plan is with your attorney for a closer look')).toBeNull(),
    );
    const nameField = (await screen.findByLabelText(/Full legal name/)) as HTMLInputElement;
    await waitFor(() => expect(nameField.disabled).toBe(false));
  });

  it('H1: Submit also flushes first, without keepalive', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    const nameField = await screen.findByLabelText(/Full legal name/);
    fireEvent.change(nameField, { target: { value: 'Submit Edit' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit for attorney review' }));
    await waitFor(() => expect(control.events).toContain('submit'));
    expect(control.events).toEqual(['put', 'submit']);
    // H-A: keepalive is for unload flushes only — the 64 KB ceiling
    // must never gate an ordinary save.
    expect(control.putOpts[0]?.keepalive).toBe(false);
  });

  it('H1: a failed flush aborts Submit too', async () => {
    control.putShouldFail = true;
    render(<IntakeWizard matterId="matter-1" />);
    const nameField = await screen.findByLabelText(/Full legal name/);
    fireEvent.change(nameField, { target: { value: 'Unsaved Edit' } });
    fireEvent.click(screen.getByRole('button', { name: 'Submit for attorney review' }));
    expect(
      await screen.findByText(/couldn't be saved, so nothing was submitted/),
    ).toBeDefined();
    expect(control.events).not.toContain('submit');
  });

  it('L7: after a failed flush, the next Check retries the SAME edit and succeeds', async () => {
    control.putShouldFail = true;
    render(<IntakeWizard matterId="matter-1" />);
    const nameField = await screen.findByLabelText(/Full legal name/);
    fireEvent.change(nameField, { target: { value: 'Retry Me' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check my answers' }));
    expect(
      await screen.findByText(/couldn't be saved, so the check didn't run/),
    ).toBeDefined();
    control.putShouldFail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Check my answers' }));
    expect(await screen.findByText(/All 5 required answers are in/)).toBeDefined();
    const payloads = (api.putAnswers as ReturnType<typeof vi.fn>).mock.calls.map(
      (call) => call[1] as Record<string, unknown>,
    );
    expect(payloads[payloads.length - 1]).toMatchObject({ full_name: 'Retry Me' });
    // Exactly: one failed PUT, one retried PUT, one evaluation.
    expect(control.events).toEqual(['put', 'put', 'eval']);
  });

  it('H-A: an oversized answers map is NOT sent with keepalive, even at unload', async () => {
    const { unmount } = render(<IntakeWizard matterId="matter-1" />);
    const nameField = await screen.findByLabelText(/Full legal name/);
    fireEvent.change(nameField, { target: { value: 'x'.repeat(61_000) } });
    unmount();
    await waitFor(() => expect(control.putOpts).toHaveLength(1));
    expect(control.putOpts[0]?.keepalive).toBe(false);
  });

  it('M-A: a rejected evaluate unlocks the UI again', async () => {
    control.evaluateThrows = true;
    render(<IntakeWizard matterId="matter-1" />);
    const nameField = (await screen.findByLabelText(/Full legal name/)) as HTMLInputElement;
    fireEvent.click(screen.getByRole('button', { name: 'Check my answers' }));
    expect(await screen.findByText(/evaluate exploded/)).toBeDefined();
    await waitFor(() => expect(nameField.disabled).toBe(false));
    expect(
      (screen.getByRole('button', { name: 'Check my answers' }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it('F1: Submit cannot start while a Check is in flight', async () => {
    let releaseEvaluate: (value: unknown) => void = () => undefined;
    control.evaluateDeferred = new Promise((resolve) => {
      releaseEvaluate = resolve;
    });
    render(<IntakeWizard matterId="matter-1" />);
    await screen.findByLabelText(/Full legal name/);
    fireEvent.click(screen.getByRole('button', { name: 'Check my answers' }));
    await waitFor(() => expect(control.events).toContain('eval'));
    const submitButton = screen.getByRole('button', {
      name: 'Submit for attorney review',
    }) as HTMLButtonElement;
    expect(submitButton.disabled).toBe(true);
    fireEvent.click(submitButton);
    releaseEvaluate(OK_EVALUATION);
    expect(await screen.findByText(/All 5 required answers are in/)).toBeDefined();
    expect(control.events).not.toContain('submit');
  });

  it('L7: a progress-refresh failure after a successful save does not fail the check', async () => {
    control.failProgressAfterPut = true;
    render(<IntakeWizard matterId="matter-1" />);
    const nameField = await screen.findByLabelText(/Full legal name/);
    fireEvent.change(nameField, { target: { value: 'Saved Fine' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check my answers' }));
    expect(await screen.findByText(/All 5 required answers are in/)).toBeDefined();
    expect(control.events).toEqual(['put', 'eval']);
  });

  it('H-A: the unmount flush uses keepalive; ordinary saves do not', async () => {
    const { unmount } = render(<IntakeWizard matterId="matter-1" />);
    const nameField = await screen.findByLabelText(/Full legal name/);
    fireEvent.change(nameField, { target: { value: 'Unload Edit' } });
    unmount();
    await waitFor(() => expect(control.putOpts).toHaveLength(1));
    expect(control.putOpts[0]?.keepalive).toBe(true);
  });

  it('L6: editing after a verdict clears the verdict', async () => {
    render(<IntakeWizard matterId="matter-1" />);
    const nameField = await screen.findByLabelText(/Full legal name/);
    fireEvent.click(screen.getByRole('button', { name: 'Check my answers' }));
    expect(await screen.findByText(/All 5 required answers are in/)).toBeDefined();
    fireEvent.change(nameField, { target: { value: 'Changed My Mind' } });
    await waitFor(() =>
      expect(screen.queryByText(/All 5 required answers are in/)).toBeNull(),
    );
  });
});
