/** @vitest-environment jsdom */
/**
 * R2 (ii): stale Documents tab.
 *
 * After releases succeeded, the tab kept showing "0 of N packages
 * released" with every release button still offered, because PackageCard
 * never told the tab to refetch and the card ignored pkg.status. After a
 * release completes the tab must refetch packages, render the true
 * released count, and drop the release affordance for released packages.
 * A failed (409) release must surface visibly via the release error.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

vi.mock('@/lib/api-client', () => {
  class MockApiError extends Error {
    readonly status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
    get isPermissionDenied(): boolean {
      return this.status === 403;
    }
  }
  return {
    ApiError: MockApiError,
    apiFetchBlob: vi.fn(),
    firmApi: {
      listTemplates: vi.fn(),
      listPackages: vi.fn(),
      listAmendmentRequests: vi.fn(),
      createPackage: vi.fn(),
      generateDraft: vi.fn(),
      releaseProduction: vi.fn(),
      downloadNotaryPackage: vi.fn(),
      createAmendmentRequest: vi.fn(),
    },
  };
});

import { DocumentsTab } from '../components/firm/documents-tab';
import { apiFetchBlob, firmApi, ApiError, type DocumentPackage } from '@/lib/api-client';

const listPackages = vi.mocked(firmApi.listPackages);
const releaseProduction = vi.mocked(firmApi.releaseProduction);

const pkg: DocumentPackage = {
  id: 'pkg-1',
  firm_id: 'firm-1',
  matter_id: 'matter-1',
  template_id: 'tpl-1',
  status: 'assembled',
  created_at: '2026-10-27T12:00:00Z',
};

async function releaseViaUi(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Release for production' }));
  await user.click(await screen.findByRole('button', { name: 'Confirm release' }));
}

describe('DocumentsTab release refetch (R2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // jsdom has no object-URL implementation; the download path uses it.
    URL.createObjectURL = vi.fn(() => 'blob:final');
    URL.revokeObjectURL = vi.fn();
    vi.mocked(firmApi.listTemplates).mockResolvedValue([]);
    vi.mocked(firmApi.listAmendmentRequests).mockResolvedValue([]);
    vi.mocked(apiFetchBlob).mockResolvedValue({
      blob: new Blob(['final'], { type: 'application/pdf' }),
      filename: 'final.pdf',
    });
  });

  it('refetches after a successful release and renders the true released state', async () => {
    const user = userEvent.setup();
    const releasedPkg = { ...pkg, status: 'released' };
    listPackages
      .mockResolvedValueOnce([pkg])
      .mockResolvedValue([releasedPkg]);
    releaseProduction.mockResolvedValue({
      package: releasedPkg,
      versions: [
        {
          id: 'ver-1',
          package_id: 'pkg-1',
          file_format: 'pdf',
          download_url: '/v1/document-versions/ver-1/download',
        },
      ],
    } as Awaited<ReturnType<typeof releaseProduction>>);

    render(<DocumentsTab matterId="matter-1" />);

    expect(await screen.findByText('0 of 1 packages released.')).toBeDefined();

    await releaseViaUi(user);

    // Refetch happened and the count reflects the server state…
    await waitFor(() => expect(listPackages).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('1 of 1 packages released.')).toBeDefined();
    // …and the released package no longer offers a duplicate release.
    expect(screen.queryByRole('button', { name: 'Release for production' })).toBeNull();
    expect(screen.getAllByText('Released').length).toBeGreaterThan(0);
  });

  it('surfaces a 409 release failure visibly and keeps the true (unreleased) state', async () => {
    const user = userEvent.setup();
    listPackages.mockResolvedValue([pkg]);
    releaseProduction.mockRejectedValue(
      new ApiError(409, 'Final documents can only be released after the attorney approves the document release gate for this matter.'),
    );

    render(<DocumentsTab matterId="matter-1" />);

    expect(await screen.findByText('0 of 1 packages released.')).toBeDefined();

    await releaseViaUi(user);

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('document release gate');
    expect(screen.getByText('0 of 1 packages released.')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Release for production' })).toBeDefined();
  });
});
