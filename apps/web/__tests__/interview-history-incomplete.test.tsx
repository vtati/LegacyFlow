/** @vitest-environment jsdom */
/**
 * ERR-009 (web half): when a turn response carries
 * `history_incomplete: true` (some earlier turn bodies couldn't be
 * decrypted into the model's context), the interview shows a
 * non-blocking notice — saved answers are unaffected.
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
    postInterviewTurn: vi.fn(async () => ({
      id: 'turn-2',
      session_id: 'sess-1',
      role: 'assistant',
      reply: 'Thanks — noted.',
      citations: [],
      proposals: [],
      refused: false,
      escalated: false,
      trace_hash: 'abc',
      completeness_pct: 55,
      missing_required_keys: [],
      parked_required_keys: [],
      replayed: false,
      history_incomplete: true,
    })),
  },
}));

import { InterviewChat } from '../components/family/interview/interview-chat';

describe('InterviewChat history-incomplete notice (ERR-009)', () => {
  it('shows the non-blocking notice after a flagged turn response', async () => {
    const user = userEvent.setup();
    render(<InterviewChat matterId="matter-1" />);

    const composer = await screen.findByLabelText(/Message the intake assistant/i);
    expect(screen.queryByText(/couldn.t be loaded/i)).toBeNull();

    await user.type(composer, 'Hello');
    await user.click(screen.getByRole('button', { name: /^send$/i }));

    expect(
      await screen.findByText(
        /Part of your earlier conversation couldn.t be loaded — your saved answers are unaffected\./,
      ),
    ).toBeDefined();
    // Non-blocking: the conversation itself continued.
    expect(await screen.findByText('Thanks — noted.')).toBeDefined();
  });
});
