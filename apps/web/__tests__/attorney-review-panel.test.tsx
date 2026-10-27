/** @vitest-environment jsdom */
/**
 * Attorney Reviewer panel (plan 2026-10-27, design §9.6/§9.7).
 *
 * Contracts pinned here:
 * - The panel is absent (not errored, not empty) when the firm flag is
 *   off or no data has loaded.
 * - Findings render grouped by document with plain-language severity
 *   labels; the copy frames an "automated pre-review" and never claims
 *   a legal review.
 * - Acknowledging sends the finding id + the typed note; an already
 *   acknowledged finding shows its note instead of the form.
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { AttorneyReviewPanel } from '../components/family/review/attorney-review-panel';
import type { AttorneyReviewFindings } from '../lib/api-client';

const baseData: AttorneyReviewFindings = {
  enabled: true,
  run: {
    id: 'run-1',
    status: 'completed',
    model_id: 'gpt-5.5',
    prompt_version: 'attorney_review-v1',
    source_fingerprint: 'fp-1',
    created_at: '2026-10-27T12:00:00Z',
    completed_at: '2026-10-27T12:00:05Z',
  },
  findings: [
    {
      id: 'finding-1',
      layer: 'deterministic',
      check: 'name_consistency',
      severity: 'should_fix',
      location: { document: 'poa', field: null, excerpt: 'Neha Kapur' },
      explanation:
        'The name "Neha Kapur" in this document differs from "Neha Kapoor" used elsewhere.',
      suggested_fix: 'Make the spelling match: "Neha Kapoor".',
      disposition: 'open',
      acknowledgment_note: null,
      acknowledged_by_user_id: null,
      acknowledged_by_actor: null,
      acknowledged_at: null,
    },
    {
      id: 'finding-2',
      layer: 'model',
      check: 'missing_context',
      severity: 'question_for_attorney',
      location: { document: 'will', field: null, excerpt: 'my house in Florida' },
      explanation: 'The will mentions a house in Florida — is there property in another state?',
      suggested_fix: 'Ask the attorney whether this needs recording.',
      disposition: 'acknowledged',
      acknowledgment_note: 'Client confirmed the Florida house was sold in 2024.',
      acknowledged_by_user_id: 'user-1',
      acknowledged_by_actor: 'firm',
      acknowledged_at: '2026-10-27T13:00:00Z',
    },
  ],
};

describe('AttorneyReviewPanel', () => {
  it('renders nothing when disabled or not loaded', () => {
    const { container: c1 } = render(
      <AttorneyReviewPanel data={null} onAcknowledge={vi.fn()} />,
    );
    expect(c1.firstChild).toBeNull();
    const { container: c2 } = render(
      <AttorneyReviewPanel
        data={{ enabled: false, run: null, findings: [] }}
        onAcknowledge={vi.fn()}
      />,
    );
    expect(c2.firstChild).toBeNull();
  });

  it('renders findings with advisory framing and severity labels', () => {
    render(<AttorneyReviewPanel data={baseData} onAcknowledge={vi.fn()} />);
    expect(screen.getByText('Automated pre-review')).toBeTruthy();
    expect(screen.getByText(/isn.t a legal review/i)).toBeTruthy();
    expect(screen.getByText('Worth fixing')).toBeTruthy();
    expect(screen.getByText('Question for your attorney')).toBeTruthy();
    expect(screen.getAllByText(/Neha Kapur/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Make the spelling match/)).toBeTruthy();
    // The acknowledged finding shows its note, not a form.
    expect(
      screen.getByText(/Florida house was sold in 2024/),
    ).toBeTruthy();
  });

  it('acknowledging sends the finding id and the typed note', () => {
    const onAcknowledge = vi.fn();
    render(<AttorneyReviewPanel data={baseData} onAcknowledge={onAcknowledge} />);
    const input = screen.getByLabelText(/Note for your attorney/i);
    fireEvent.change(input, { target: { value: 'The shorter spelling is intentional.' } });
    fireEvent.click(screen.getByRole('button', { name: /acknowledge/i }));
    expect(onAcknowledge).toHaveBeenCalledWith(
      'finding-1',
      'The shorter spelling is intentional.',
    );
  });

  it('shows who acknowledged a finding', () => {
    render(<AttorneyReviewPanel data={baseData} onAcknowledge={vi.fn()} />);
    expect(screen.getByText(/by the firm’s team/)).toBeTruthy();
  });

  it('does not claim a full pass when only the deterministic checks ran', () => {
    render(
      <AttorneyReviewPanel
        data={{
          enabled: true,
          run: { ...baseData.run!, model_id: 'deterministic_only' },
          findings: [],
        }}
        onAcknowledge={vi.fn()}
      />,
    );
    expect(screen.getByText(/judgment pass runs when you submit/i)).toBeTruthy();
  });

  it('surfaces action errors instead of failing silently', () => {
    render(
      <AttorneyReviewPanel
        data={baseData}
        onAcknowledge={vi.fn()}
        actionError="That acknowledgment could not be recorded — please try again."
      />,
    );
    expect(screen.getByRole('alert').textContent).toMatch(/could not be recorded/);
  });

  it('offers the family a manual re-run when no run exists', () => {
    const onRun = vi.fn();
    render(
      <AttorneyReviewPanel
        data={{ enabled: true, run: null, findings: [] }}
        onAcknowledge={vi.fn()}
        onRun={onRun}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /run pre-review/i }));
    expect(onRun).toHaveBeenCalled();
  });

  it('shows a clean result when a completed run found nothing', () => {
    render(
      <AttorneyReviewPanel
        data={{ enabled: true, run: baseData.run, findings: [] }}
        onAcknowledge={vi.fn()}
      />,
    );
    expect(screen.getByText(/no issues found/i)).toBeTruthy();
  });
});
