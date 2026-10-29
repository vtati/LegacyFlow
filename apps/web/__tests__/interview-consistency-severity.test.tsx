/** @vitest-environment jsdom */
/**
 * UI-014 (interview surface): consistency findings carry backend
 * severities `review` / `attention` only. The panel must render the
 * severity as visible text with truthful styling — never the old
 * /high|critical|urgent/ regex that sent every finding to success green.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { InterviewSession } from '../lib/api-client';

const session: InterviewSession = {
  id: 'sess-1',
  matter_id: 'matter-1',
  question_set_id: 'household',
  pack_version: '2.0.0',
  status: 'in_progress',
  completeness_pct: 50,
  missing_required_keys: [],
  parked_required_keys: [],
  checklist: [],
  open_proposal_count: 0,
  turn_count: 1,
  created_at: '2026-10-26T00:00:00Z',
  updated_at: '2026-10-26T00:00:00Z',
};

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  api: {
    getQuestionSets: vi.fn(async () => ({ question_sets: { household: {} } })),
    startInterviewSession: vi.fn(async () => session),
    getInterviewSession: vi.fn(async () => session),
    listInterviewTurns: vi.fn(async () => []),
    listInterviewProposals: vi.fn(async () => []),
    listInterviewExtractions: vi.fn(async () => []),
    runInterviewConsistencyReview: vi.fn(async () => ({
      review_id: 'rev-1',
      escalations_created: 0,
      facts_considered: 3,
      findings: [
        { question: 'Spouse name differs between answers', fields: ['spouse_full_name'], severity: 'attention' },
        { question: 'Residence state worth a second look', fields: ['primary_residence_state'], severity: 'review' },
        { question: 'An unusual one', fields: ['notes'], severity: 'something_else' },
      ],
    })),
  },
}));

import { InterviewChat } from '../components/family/interview/interview-chat';

describe('InterviewChat consistency severity display (UI-014)', () => {
  it('renders severity label text with truthful styling per finding', async () => {
    const user = userEvent.setup();
    render(<InterviewChat matterId="matter-1" />);

    await user.click(await screen.findByRole('button', { name: /run consistency check/i }));

    const attentionLabel = await screen.findByText('Needs attention');
    expect(attentionLabel).toBeDefined();
    // The attention finding's card is warning-styled, never success green.
    const attentionCard = attentionLabel.closest('div');
    expect(attentionCard?.className).toContain('warning');
    expect(attentionCard?.className).not.toContain('success');
    expect(screen.getByText('Spouse name differs between answers')).toBeDefined();

    const reviewLabel = screen.getByText('For review');
    expect(reviewLabel.closest('div')?.className).not.toContain('success');

    // Unknown severities render neutrally with the raw label title-cased.
    expect(screen.getByText('Something Else')).toBeDefined();
  });
});
