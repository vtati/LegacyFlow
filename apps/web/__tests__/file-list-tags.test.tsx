/** @vitest-environment jsdom */
/**
 * Stage F / F3 follow-up: file-list addFileTags.
 *
 * trackSave now clears the header save indicator silently on a 4xx
 * rejection (the calling surface owns the truthful message) — but this
 * call site had NO local error surface at all: FileList.handleAddTag let
 * the rejection propagate out of the row's submit handler, so a failed
 * tag add showed the user nothing. It must surface its own inline error
 * for ANY failure (4xx, 5xx, network), and the success path must stay
 * exactly as before (input clears, parent is handed the updated file).
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
    api: {
      addFileTags: vi.fn(),
      analyzeFile: vi.fn(),
    },
  };
});

import { FileList } from '../components/files/file-list';
import { api, ApiError, type MatterFile } from '@/lib/api-client';

const addFileTags = vi.mocked(api.addFileTags);

const analyzedFile: MatterFile = {
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
  document_type: 'will',
  ai_summary: 'Last will and testament.',
  key_facts: [],
  tags: ['estate'],
  analysis_confidence: 0.9,
  analyzed_at: '2026-10-26T12:00:00Z',
  created_at: '2026-10-26T12:00:00Z',
  updated_at: '2026-10-26T12:00:00Z',
};

async function renderAndOpenTagForm(onAnalyzed = vi.fn()) {
  const user = userEvent.setup();
  render(
    <FileList
      files={[analyzedFile]}
      downloadingId={null}
      onDownload={vi.fn()}
      onAnalyzed={onAnalyzed}
      allowAnalyze
    />,
  );
  await user.click(screen.getByRole('button', { name: 'Show analysis details' }));
  const input = screen.getByLabelText('Add a tag');
  await user.type(input, 'reviewed');
  return { user, input, onAnalyzed };
}

describe('FileList add tag (Stage F F3 follow-up)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows a user-visible error when addFileTags is rejected with a 4xx', async () => {
    addFileTags.mockRejectedValue(new ApiError(422, 'Tag limit reached for this file.'));
    const { user, input, onAnalyzed } = await renderAndOpenTagForm();

    await user.click(screen.getByRole('button', { name: 'Add' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Tag limit reached for this file.');
    // Nothing was added: parent not handed an update, typed tag kept.
    expect(onAnalyzed).not.toHaveBeenCalled();
    expect(input).toHaveValue('reviewed');
  });

  it('shows a user-visible error when addFileTags fails with a 5xx / network failure', async () => {
    addFileTags.mockRejectedValue(new TypeError('Failed to fetch'));
    const { user, onAnalyzed } = await renderAndOpenTagForm();

    await user.click(screen.getByRole('button', { name: 'Add' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).not.toBe('');
    expect(onAnalyzed).not.toHaveBeenCalled();
  });

  it('success path unchanged: resolved add hands the updated file up and clears the input', async () => {
    const updated: MatterFile = { ...analyzedFile, tags: ['estate', 'reviewed'] };
    addFileTags.mockResolvedValue(updated);
    const { user, input, onAnalyzed } = await renderAndOpenTagForm();

    await user.click(screen.getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(input).toHaveValue(''));
    expect(addFileTags).toHaveBeenCalledWith('file-1', ['reviewed']);
    expect(onAnalyzed).toHaveBeenCalledWith(updated);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
