/** @vitest-environment jsdom */
/**
 * UI-017 (firm surface): the matter-detail tabs already had their aria
 * wired; this pins the missing keyboard half of the tab pattern —
 * ArrowLeft/ArrowRight/Home/End move selection and focus, with roving
 * tabindex.
 */
import { Suspense } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    status = 0;
  },
  api: { getHousehold: vi.fn(async () => null) },
  firmApi: {
    getMatter: vi.fn(async () => ({
      matter: {
        id: 'matter-1',
        title: 'Sharma plan',
        status: 'intake_in_progress',
        jurisdiction_code: 'US-VA',
        assigned_attorney_id: null,
      },
      household: { id: 'hh-1', name: 'Sharma household' },
    })),
  },
}));

// Tab bodies are irrelevant to the tab-strip contract; stub them out.
vi.mock('@/components/ai/assistant-panel', () => ({ AssistantPanel: () => null }));
vi.mock('@/components/firm/assign-attorney', () => ({ AssignAttorney: () => null }));
vi.mock('@/components/firm/review-decision-panel', () => ({ ReviewDecisionPanel: () => null }));
vi.mock('@/components/firm/matter-overview', () => ({ MatterOverview: () => null }));
vi.mock('@/components/firm/review-packet-tab', () => ({ ReviewPacketTab: () => null }));
vi.mock('@/components/firm/comments-tab', () => ({ CommentsTab: () => null }));
vi.mock('@/components/firm/clarifications-tab', () => ({ ClarificationsTab: () => null }));
vi.mock('@/components/firm/gates-tab', () => ({ GatesTab: () => null }));
vi.mock('@/components/firm/files-tab', () => ({ FilesTab: () => null }));
vi.mock('@/components/firm/documents-tab', () => ({ DocumentsTab: () => null }));
vi.mock('@/components/firm/signing-tab', () => ({ SigningTab: () => null }));
vi.mock('@/components/firm/funding-tab', () => ({ FundingTab: () => null }));

import FirmMatterDetailPage from '../app/(app)/firm/matters/[id]/page';

describe('Firm matter tabs keyboard pattern (UI-017)', () => {
  it('moves selection and focus with arrows/Home/End and roves tabindex', async () => {
    const user = userEvent.setup();
    // React's use() needs a tracked (status-pinned) promise in this
    // harness — a bare Promise.resolve suspends forever under jsdom.
    const paramsPromise = Object.assign(Promise.resolve({ id: 'matter-1' }), {
      status: 'fulfilled' as const,
      value: { id: 'matter-1' },
    });
    render(
      <Suspense fallback={null}>
        <FirmMatterDetailPage params={paramsPromise} />
      </Suspense>,
    );

    const overview = await screen.findByRole('tab', { name: 'Overview' });
    const files = screen.getByRole('tab', { name: 'Files' });
    expect(overview).toHaveAttribute('tabindex', '0');
    expect(files).toHaveAttribute('tabindex', '-1');
    // aria pairing was already wired — keep it pinned.
    expect(overview.getAttribute('aria-controls')).toBe('tab-panel-overview');

    overview.focus();
    await user.keyboard('{ArrowRight}');
    expect(files).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(files);
    expect(files).toHaveAttribute('tabindex', '0');

    await user.keyboard('{End}');
    const gates = screen.getByRole('tab', { name: 'Approval gates' });
    expect(gates).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(gates);

    await user.keyboard('{Home}');
    expect(overview).toHaveAttribute('aria-selected', 'true');
    expect(document.activeElement).toBe(overview);
  });
});
