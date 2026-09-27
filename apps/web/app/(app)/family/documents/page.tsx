'use client';

import { useCallback, useEffect, useState } from 'react';
import { Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { MatterSelect, useMatters } from '@/components/family/matter-context';
import { WatermarkNotice } from '@/components/documents/watermark-notice';
import { api, type DemoDocument } from '@/lib/api-client';
import { formatDateTime } from '@/lib/format';

/** Family documents — view and download watermarked demo documents. */
export default function FamilyDocumentsPage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [matterId, setMatterId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<DemoDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const loadDocuments = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    api
      .listDemoDocuments(id)
      .then(setDocuments)
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!matterId && matters.length > 0) {
      setMatterId(matters[0]?.id ?? null);
    }
  }, [matters, matterId]);

  useEffect(() => {
    if (matterId) loadDocuments(matterId);
  }, [matterId, loadDocuments]);

  const handleDownload = async (document: DemoDocument) => {
    setDownloadingId(document.id);
    setDownloadError(null);
    try {
      // Fetch with auth (apiFetchBlob handles the token), then trigger a browser download.
      const { blob, filename } = await api.downloadDemoDocument(document.download_url);
      const url = window.URL.createObjectURL(blob);
      const anchor = window.document.createElement('a');
      anchor.href = url;
      anchor.download = filename || document.file_name;
      window.document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setDownloadError(
        err instanceof Error
          ? `Could not download: ${err.message}`
          : 'Could not download the document.',
      );
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Documents</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          Demo versions of your plan documents, prepared by your firm for review.
        </p>
      </div>

      <WatermarkNotice detail="These are watermarked demos for review only — not legal documents. Nothing here is final until your attorney approves it." />

      {mattersLoading && <LoadingState lines={3} label="Loading your matters…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={reloadMatters} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your documents will appear here once your firm starts a plan for your household."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && (
        <MatterSelect matters={matters} value={matterId} onChange={setMatterId} />
      )}

      {matterId && loading && <LoadingState lines={4} label="Loading documents…" />}
      {error != null && matterId != null && !loading ? (
        <ErrorState error={error} onRetry={() => loadDocuments(matterId)} />
      ) : null}

      {matterId && !loading && !error && (
        <section aria-label="Demo documents" className="space-y-3">
          {downloadError && (
            <p role="alert" className="text-sm text-lf-error-700">
              {downloadError}
            </p>
          )}
          {documents.length === 0 ? (
            <EmptyState
              title="No demo documents yet"
              description="Your firm will share watermarked demo documents here once your plan takes shape."
            />
          ) : (
            <Card>
              <CardContent className="p-0">
                <ul className="divide-y divide-lf-border">
                  {documents.map((document) => (
                    <li
                      key={document.id}
                      className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-lf-text">
                          {document.file_name}
                        </p>
                        <p className="text-xs text-lf-text-subtle">
                          Demo · v{document.version} · {formatDateTime(document.created_at)}
                        </p>
                      </div>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={downloadingId === document.id}
                        onClick={() => handleDownload(document)}
                      >
                        <Download className="size-4" aria-hidden="true" />
                        {downloadingId === document.id ? 'Preparing…' : 'Download demo'}
                      </Button>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </section>
      )}
    </div>
  );
}
