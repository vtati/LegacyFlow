/** @vitest-environment jsdom */
/**
 * Mobile interview chat UX regressions (2026-10-27, reported from iPhone):
 *
 * 1. History opens at the TOP instead of pinned to the latest message.
 *    Bootstrap hydrates `messages` while the phase is still 'loading' (the
 *    thread is not mounted, so the scroll effect no-ops), then flips to
 *    'ready' — the effect must run for that commit too.
 * 2. Focus is lost after every send: the composer is `disabled` while
 *    sending, which blurs it in real browsers, and nothing restores focus
 *    afterwards — on mobile the keyboard dismisses and the layout jumps.
 *    The fix must return focus to the composer when the send completes.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type {
  InterviewSession,
  InterviewTurn,
  InterviewTurnSummary,
} from '../lib/api-client';

const SESSION: InterviewSession = {
  id: 'sess-1',
  matter_id: 'matter-1',
  question_set_id: 'household',
  pack_version: '2.0.0',
  status: 'in_progress',
  completeness_pct: 40,
  missing_required_keys: [],
  parked_required_keys: [],
  checklist: [],
  open_proposal_count: 0,
  turn_count: 6,
  created_at: '2026-10-27T00:00:00Z',
  updated_at: '2026-10-27T00:00:00Z',
};

/** API returns newest-first; the component reverses for display. */
const TURNS_NEWEST_FIRST: InterviewTurnSummary[] = [
  { id: 't6', role: 'assistant', summary: 'Assistant message three', created_at: '2026-10-27T00:06:00Z' },
  { id: 't5', role: 'user', summary: 'User message three', created_at: '2026-10-27T00:05:00Z' },
  { id: 't4', role: 'assistant', summary: 'Assistant message two', created_at: '2026-10-27T00:04:00Z' },
  { id: 't3', role: 'user', summary: 'User message two', created_at: '2026-10-27T00:03:00Z' },
  { id: 't2', role: 'assistant', summary: 'Assistant message one', created_at: '2026-10-27T00:02:00Z' },
  { id: 't1', role: 'user', summary: 'User message one', created_at: '2026-10-27T00:01:00Z' },
];

const hoisted = vi.hoisted(() => ({
  postInterviewTurn: vi.fn(),
  extractionsDelayMs: 0,
}));

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  api: {
    getQuestionSets: vi.fn(async () => ({ question_sets: { household: {} } })),
    startInterviewSession: vi.fn(async () => SESSION),
    getInterviewSession: vi.fn(async () => SESSION),
    listInterviewTurns: vi.fn(async () => TURNS_NEWEST_FIRST),
    listInterviewProposals: vi.fn(async () => []),
    listInterviewExtractions: vi.fn(
      () =>
        new Promise<[]>((resolve) =>
          setTimeout(() => resolve([]), hoisted.extractionsDelayMs),
        ),
    ),
    postInterviewTurn: hoisted.postInterviewTurn,
  },
}));

import { InterviewChat } from '../components/family/interview/interview-chat';

const THREAD_H = 1200;
const THREAD_VIEW = 500;

let origScrollHeight: PropertyDescriptor | undefined;
let origClientHeight: PropertyDescriptor | undefined;

function stubThreadMetrics() {
  origScrollHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollHeight');
  origClientHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight');
  const isThread = function (this: HTMLElement) {
    return this.classList?.contains('overflow-y-auto') ?? false;
  };
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
    configurable: true,
    get(this: HTMLElement) {
      return isThread.call(this) ? THREAD_H : 0;
    },
  });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get(this: HTMLElement) {
      return isThread.call(this) ? THREAD_VIEW : 0;
    },
  });
}

function restoreThreadMetrics() {
  if (origScrollHeight) Object.defineProperty(HTMLElement.prototype, 'scrollHeight', origScrollHeight);
  if (origClientHeight) Object.defineProperty(HTMLElement.prototype, 'clientHeight', origClientHeight);
}

function threadEl(container: HTMLElement): HTMLElement {
  const el = container.querySelector<HTMLElement>('div[aria-live="polite"].overflow-y-auto');
  if (!el) throw new Error('thread not rendered');
  return el;
}

beforeEach(() => {
  hoisted.postInterviewTurn.mockReset();
  hoisted.extractionsDelayMs = 0;
});

/** jsdom keeps focus on a disabled element and ignores blur() on it; real
 * browsers move focus to <body> when the focused element becomes disabled.
 * Simulate that mechanic by focusing the body explicitly. */
function simulateBrowserFocusLoss() {
  document.body.setAttribute('tabindex', '-1');
  document.body.focus();
}

afterEach(() => {
  restoreThreadMetrics();
});

describe('InterviewChat mobile UX', () => {
  it('opens pinned to the latest message once history is hydrated', async () => {
    stubThreadMetrics();
    // Bootstrap hydrates messages, then awaits extractions before flipping
    // phase to 'ready' — with a slow extractions call the messages commit
    // renders while the thread is still unmounted (phase 'loading'), exactly
    // as on a real network. The pin must still land once the thread mounts.
    hoisted.extractionsDelayMs = 60;
    const { container } = render(<InterviewChat matterId="matter-1" />);
    // Wait until the chat is fully ready (composer rendered after bootstrap).
    const composer = await screen.findByLabelText('Message the intake assistant');
    expect(composer).toBeTruthy();
    await waitFor(() => {
      expect(threadEl(container).scrollTop).toBe(THREAD_H);
    });
  });

  it('returns focus to the composer after a send completes', async () => {
    stubThreadMetrics();
    let resolveTurn: ((t: InterviewTurn) => void) | null = null;
    hoisted.postInterviewTurn.mockImplementation(
      () =>
        new Promise<InterviewTurn>((resolve) => {
          resolveTurn = resolve;
        }),
    );
    render(<InterviewChat matterId="matter-1" />);
    const composer = (await screen.findByLabelText(
      'Message the intake assistant',
    )) as HTMLTextAreaElement;
    composer.focus();
    expect(document.activeElement).toBe(composer);
    fireEvent.change(composer, { target: { value: 'Hello, my name is Test.' } });
    fireEvent.keyDown(composer, { key: 'Enter' });
    // While sending, the composer becomes disabled; real browsers move
    // focus to <body> when the focused element is disabled. Simulate that
    // browser mechanic, which jsdom does not perform on its own.
    await waitFor(() => expect(composer.disabled).toBe(true));
    simulateBrowserFocusLoss();
    expect(document.activeElement).not.toBe(composer);
    resolveTurn!({
      id: 't7',
      session_id: 'sess-1',
      role: 'assistant',
      reply: 'Thanks — and where do you live?',
      citations: [],
      proposals: [],
      refused: false,
      escalated: false,
      trace_hash: 'x',
      completeness_pct: 45,
      missing_required_keys: [],
      parked_required_keys: [],
      replayed: false,
    });
    await screen.findByText('Thanks — and where do you live?');
    await waitFor(() => {
      expect(composer.disabled).toBe(false);
      expect(document.activeElement).toBe(composer);
    });
  });

  it('keeps focus in the composer when a send fails and the draft is restored', async () => {
    stubThreadMetrics();
    hoisted.postInterviewTurn.mockRejectedValue(new Error('network down'));
    render(<InterviewChat matterId="matter-1" />);
    const composer = (await screen.findByLabelText(
      'Message the intake assistant',
    )) as HTMLTextAreaElement;
    composer.focus();
    fireEvent.change(composer, { target: { value: 'Draft that fails' } });
    fireEvent.keyDown(composer, { key: 'Enter' });
    await waitFor(() => expect(composer.disabled).toBe(true));
    simulateBrowserFocusLoss();
    await waitFor(() => expect(composer.disabled).toBe(false));
    expect((composer as HTMLTextAreaElement).value).toBe('Draft that fails');
    expect(document.activeElement).toBe(composer);
  });
});
