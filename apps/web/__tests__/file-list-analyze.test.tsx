/** @vitest-environment jsdom */
/**
 * Stage F / Track 2c: file-list analyzeFile.
 *
 * FileList.handleAnalyze catches failures into `analyzeError` state, but
 * FileRow rendered that error only under
 * `analyzeError != null && analyzingId === file.id` — while handleAnalyze's
 * `finally` sets analyzingId back to null. The error could therefore NEVER
 * render. And api.analyzeFile is called with `track: false`, so there is
 * no save-pill either: a failed Analyze was completely silent.
 *
 * Fix: keep an `analyzeErrorId` alongside the message and gate rendering
 * on the error's file id instead of the transient analyzingId, so the
 * error renders for the right file and persists until the next attempt.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
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
    api: {
      addFileTags: vi.fn(),
      analyzeFile: vi.fn(),
    },
  };
});

import { FileList } from '../components/files/file-list';
import { api, ApiError, type MatterFile } from '@/lib/api-client';

const analyzeFile = vi.mocked(api.analyzeFile);

const unanalyzedFile: MatterFile = {
  id: 'file-1',
  firm_id: 'firm-1',
  household_id: 'hh-1',
  matter_id: 'matter-1',
  uploader_user_id: 'user-1',
  original_filename: 'last-will.pdf',
  stored_path: 'vault/file-1.pdf',
  mime_type: 'application/pdf',
  size_bytes: 2048,
  scan_status: 'clean',
  scan_detail: null,
  extracted_text: 'Last will and testament…',
  extraction_status: 'done',
  document_type: null,
  ai_summary: null,
  key_facts: [],
  tags: [],
  analysis_confidence: null,
  analyzed_at: null,
  created_at: '2026-10-26T12:00:00Z',
  updated_at: '2026-10-26T12:00:00Z',
};

const secondFile: MatterFile = {
  ...unanalyzedFile,
  id: 'file-2',
  original_filename: 'deed.pdf',
  stored_path: 'vault/file-2.pdf',
};

describe('FileList analyze (Stage F Track 2c)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows a visible alert when analyzeFile is rejected with a 422 and it remains after analyzing clears', async () => {
    analyzeFile.mockRejectedValue(new ApiError(422, 'Document could not be analyzed.'));
    const user = userEvent.setup();
    const onAnalyzed = vi.fn();
    render(
      <FileList
        files={[unanalyzedFile]}
        downloadingId={null}
        onDownload={vi.fn()}
        onAnalyzed={onAnalyzed}
        allowAnalyze
      />,
    );

    await user.click(screen.getByRole('button', { name: /Analyze/ }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Document could not be analyzed.');
    // Analyzing state has cleared (finally ran) — error must persist.
    expect(screen.queryByText('Analyzing…')).toBeNull();
    expect(screen.getByRole('alert')).toBe(alert);
    expect(onAnalyzed).not.toHaveBeenCalled();
  });

  it('shows a visible alert when analyzeFile fails with a network-style rejection', async () => {
    analyzeFile.mockRejectedValue(new TypeError('Failed to fetch'));
    const user = userEvent.setup();
    const onAnalyzed = vi.fn();
    render(
      <FileList
        files={[unanalyzedFile]}
        downloadingId={null}
        onDownload={vi.fn()}
        onAnalyzed={onAnalyzed}
        allowAnalyze
      />,
    );

    await user.click(screen.getByRole('button', { name: /Analyze/ }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Failed to fetch');
    expect(screen.queryByText('Analyzing…')).toBeNull();
    expect(onAnalyzed).not.toHaveBeenCalled();
  });

  it('renders the error only in the affected file row when multiple files are present', async () => {
    analyzeFile.mockRejectedValue(new ApiError(422, 'Document could not be analyzed.'));
    const user = userEvent.setup();
    render(
      <FileList
        files={[unanalyzedFile, secondFile]}
        downloadingId={null}
        onDownload={vi.fn()}
        onAnalyzed={vi.fn()}
        allowAnalyze
      />,
    );

    const rows = screen.getAllByRole('listitem');
    expect(rows).toHaveLength(2);
    // Click Analyze in the second file's row only.
    await user.click(within(rows[1]).getByRole('button', { name: /Analyze/ }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Document could not be analyzed.');
    // Exactly one alert, and it lives in the second row.
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(within(rows[1]).getByRole('alert')).toBe(alert);
    expect(within(rows[0]).queryByRole('alert')).toBeNull();
  });

  it('clears a prior analyze error at the start of a new attempt for that file', async () => {
    analyzeFile.mockRejectedValueOnce(new ApiError(422, 'Document could not be analyzed.'));
    const user = userEvent.setup();
    render(
      <FileList
        files={[unanalyzedFile]}
        downloadingId={null}
        onDownload={vi.fn()}
        onAnalyzed={vi.fn()}
        allowAnalyze
      />,
    );

    await user.click(screen.getByRole('button', { name: /Analyze/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Document could not be analyzed.',
    );

    // Second attempt succeeds — the stale error must be gone.
    analyzeFile.mockResolvedValue({
      upload_id: 'file-1',
      document_type: 'will',
      summary: 'Last will and testament.',
      key_facts: [],
      suggested_tags: ['estate'],
      confidence: 0.9,
      analyzed_at: '2026-10-26T12:00:00Z',
      analysis_source: 'model',
    });
    await user.click(screen.getByRole('button', { name: /Analyze/ }));

    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  });

  it('success path unchanged: resolved analyze hands the merged file up and shows no alert', async () => {
    analyzeFile.mockResolvedValue({
      upload_id: 'file-1',
      document_type: 'will',
      summary: 'Last will and testament.',
      key_facts: [],
      suggested_tags: ['estate'],
      confidence: 0.9,
      analyzed_at: '2026-10-26T12:00:00Z',
      analysis_source: 'model',
    });
    const user = userEvent.setup();
    const onAnalyzed = vi.fn();
    render(
      <FileList
        files={[unanalyzedFile]}
        downloadingId={null}
        onDownload={vi.fn()}
        onAnalyzed={onAnalyzed}
        allowAnalyze
      />,
    );

    await user.click(screen.getByRole('button', { name: /Analyze/ }));

    await waitFor(() => expect(onAnalyzed).toHaveBeenCalled());
    expect(analyzeFile).toHaveBeenCalledWith('file-1');
    expect(onAnalyzed).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'file-1',
        document_type: 'will',
        ai_summary: 'Last will and testament.',
        tags: ['estate'],
        analysis_confidence: 0.9,
        analyzed_at: '2026-10-26T12:00:00Z',
      }),
    );
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText('Analyzing…')).toBeNull();
  });
});
