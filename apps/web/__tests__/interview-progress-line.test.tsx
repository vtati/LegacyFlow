/** @vitest-environment jsdom */
/**
 * Progress line honesty (Phase 4 reviewer minor 7): the sidebar line
 * "All required items are collected." may render only when nothing is
 * open AND nothing is parked — parked-required items still gate
 * document generation, so while only parked items remain the line
 * must say collection is complete except the parked items.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import type { InterviewChecklistItem, InterviewSession } from '../lib/api-client';

function sessionWith(checklist: InterviewChecklistItem[]): InterviewSession {
  return {
    id: 'sess-1',
    matter_id: 'matter-1',
    question_set_id: 'household',
    pack_version: '2.0.0',
    status: 'in_progress',
    completeness_pct: 90,
    missing_required_keys: [],
    parked_required_keys: [],
    checklist,
    open_proposal_count: 0,
    turn_count: 1,
    created_at: '2026-10-26T00:00:00Z',
    updated_at: '2026-10-26T00:00:00Z',
  };
}

const hoisted = vi.hoisted(() => ({
  session: null as unknown as InterviewSession,
}));

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  api: {
    getQuestionSets: vi.fn(async () => ({ question_sets: { household: {} } })),
    startInterviewSession: vi.fn(async () => hoisted.session),
    getInterviewSession: vi.fn(async () => hoisted.session),
    listInterviewTurns: vi.fn(async () => []),
    listInterviewProposals: vi.fn(async () => []),
    listInterviewExtractions: vi.fn(async () => []),
  },
}));

import { InterviewChat } from '../components/family/interview/interview-chat';

const ANSWERED: InterviewChecklistItem = {
  question_key: 'marital_status',
  required: true,
  answered: true,
  has_open_proposal: false,
  parked: false,
};
const PARKED: InterviewChecklistItem = {
  question_key: 'primary_guardian_person',
  required: true,
  answered: false,
  has_open_proposal: false,
  parked: true,
};
const MISSING: InterviewChecklistItem = {
  question_key: 'primary_residence_state',
  required: true,
  answered: false,
  has_open_proposal: false,
  parked: false,
};

describe('InterviewChat progress line', () => {
  it('does not claim all collected while only parked items remain', async () => {
    hoisted.session = sessionWith([ANSWERED, PARKED]);
    render(<InterviewChat matterId="matter-1" />);

    expect(
      await screen.findByText(
        'Everything else is collected — just the saved-for-later items below still need your answers.',
      ),
    ).toBeDefined();
    expect(screen.queryByText('All required items are collected.')).toBeNull();
    expect(
      screen.getByText(/1 item saved for later — you will finish it before your documents are prepared\./),
    ).toBeDefined();
  });

  it('claims all collected only when nothing is open or parked', async () => {
    hoisted.session = sessionWith([ANSWERED]);
    render(<InterviewChat matterId="matter-1" />);

    expect(await screen.findByText('All required items are collected.')).toBeDefined();
    expect(screen.queryByText(/saved for later/)).toBeNull();
  });

  it('still counts unanswered items as needed', async () => {
    hoisted.session = sessionWith([ANSWERED, MISSING]);
    render(<InterviewChat matterId="matter-1" />);

    expect(await screen.findByText('1 required item still needed.')).toBeDefined();
    expect(screen.queryByText('All required items are collected.')).toBeNull();
  });
});
