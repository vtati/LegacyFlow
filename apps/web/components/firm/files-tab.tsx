'use client';

import { useCallback, useEffect, useState } from 'react';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { FileList } from '@/components/files/file-list';
import { firmApi, fileDownloadUrl, type MatterFile } from '@/lib/api-client';

/** Firm files tab — read-only matter files with scan badges and downloads. */
export function FilesTab({ matterId }: { matterId: string }): React.JSX.Element {
  const [files, setFiles] = useState<MatterFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const loadFiles = useCallback(() => {
    setLoading(true);
    setError(null);
    firmApi
      .listFiles(matterId)
      .then(setFiles)
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, [matterId]);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  const handleDownload = (file: MatterFile) => {
    setDownloadingId(file.id);
    setDownloadError(null);
    firmApi
      .getDownloadToken(file.id)
      .then((response) => {
        const anchor = document.createElement('a');
        anchor.href = fileDownloadUrl(file.id, response.token);
        anchor.download = file.original_filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      })
      .catch((err: unknown) =>
        setDownloadError(
          err instanceof Error
            ? `Could not prepare the download: ${err.message}`
            : 'Could not prepare the download.',
        ),
      )
      .finally(() => setDownloadingId(null));
  };

  if (loading) return <LoadingState lines={4} label="Loading files…" />;
  if (error != null) return <ErrorState error={error} onRetry={loadFiles} />;
  if (files.length === 0 && !loading) {
    return (
      <EmptyState
        title="No files yet"
        description="The client has not uploaded any files for this matter yet."
      />
    );
  }

  return (
    <FileList
      files={files}
      downloadingId={downloadingId}
      onDownload={handleDownload}
      downloadError={downloadError}
    />
  );
}
