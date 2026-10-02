'use client';

import { useCallback, useEffect, useState } from 'react';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { MatterSelect, useMatters } from '@/components/family/matter-context';
import { FileList } from '@/components/files/file-list';
import { UploadButton } from '@/components/files/upload-button';
import { api, fileDownloadUrl, type MatterFile } from '@/lib/api-client';

/** Family files — matter file list, client-validated upload, scan badges, downloads. */
export default function FamilyFilesPage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [matterId, setMatterId] = useState<string | null>(null);
  const [files, setFiles] = useState<MatterFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const loadFiles = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    api
      .listFiles(id)
      .then(setFiles)
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!matterId && matters.length > 0) {
      setMatterId(matters[0]?.id ?? null);
    }
  }, [matters, matterId]);

  useEffect(() => {
    if (matterId) loadFiles(matterId);
  }, [matterId, loadFiles]);

  const handleUploaded = (file: MatterFile) => {
    setFiles((prev) => [file, ...prev]);
    setNotice(`“${file.original_filename}” uploaded — text extracted, ready for analysis.`);
  };

  const handleDownload = (file: MatterFile) => {
    if (!matterId) return;
    setDownloadingId(file.id);
    setDownloadError(null);
    api
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

  const handleAnalyzed = (file: MatterFile) => {
    setFiles((prev) => prev.map((f) => (f.id === file.id ? file : f)));
  };

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Files</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Documents for your plan — upload what your attorney asks for, and download anything you
          need. Every upload is scanned before it becomes available.
        </p>
      </div>

      {mattersLoading && <LoadingState lines={3} label="Loading your matters…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={reloadMatters} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your files will appear here once your firm starts a plan for your household."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && (
        <MatterSelect matters={matters} value={matterId} onChange={setMatterId} />
      )}

      {matterId && loading && <LoadingState lines={4} label="Loading files…" />}
      {error != null && matterId != null && !loading ? (
        <ErrorState error={error} onRetry={() => loadFiles(matterId)} />
      ) : null}

      {notice && (
        <p
          role="status"
          className="rounded-lf-control border border-lf-success-600 bg-lf-success-50 px-4 py-3 text-sm text-lf-success-700"
        >
          {notice}
        </p>
      )}

      {matterId && !loading && !error && (
        <section aria-label="Matter files" className="space-y-4">
          <UploadButton matterId={matterId} onUploaded={handleUploaded} />
          <FileList
            files={files}
            downloadingId={downloadingId}
            onDownload={handleDownload}
            downloadError={downloadError}
            onAnalyzed={handleAnalyzed}
            allowAnalyze
          />
        </section>
      )}
    </div>
  );
}
