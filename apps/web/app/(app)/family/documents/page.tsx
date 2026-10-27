'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { MatterSelect, useMatters } from '@/components/family/matter-context';
import { DocumentTypeTile, documentTypeLabel } from '@/components/documents/document-type-tile';
import { WatermarkNotice } from '@/components/documents/watermark-notice';
import { api, type DraftDocument } from '@/lib/api-client';
import { formatDateTime } from '@/lib/format';

/** Family documents — view and download watermarked draft documents. */
export default function FamilyDocumentsPage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [matterId, setMatterId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<DraftDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  // Self-serve draft generation probe: when the firm has it on, the
  // header links to the review screen where drafts are created.
  const [draftsEnabled, setDraftsEnabled] = useState(false);

  useEffect(() => {
    if (!matterId) {
      setDraftsEnabled(false);
      return;
    }
    let cancelled = false;
    api
      .getDraftSetState(matterId)
      .then((state) => {
        if (!cancelled) setDraftsEnabled(state.enabled);
      })
      .catch(() => {
        if (!cancelled) setDraftsEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, [matterId]);

  const loadDocuments = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    api
      .listDraftDocuments(id)
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

  const handleDownload = async (document: DraftDocument) => {
    setDownloadingId(document.id);
    setDownloadError(null);
    try {
      // Fetch with auth (apiFetchBlob handles the token), then trigger a browser download.
      const { blob, filename } = await api.downloadDraftDocument(document.download_url);
      const url = window.URL.createObjectURL(blob);
      const isPdf = (filename || document.file_name).toLowerCase().endsWith('.pdf');
      if (isPdf) {
        // Open PDFs in a new tab for in-browser preview; fallback to download.
        const win = window.open(url, '_blank', 'noopener');
        if (!win) {
          const anchor = window.document.createElement('a');
          anchor.href = url;
          anchor.download = filename || document.file_name;
          window.document.body.appendChild(anchor);
          anchor.click();
          anchor.remove();
        }
        // Revoke after a delay so the new tab can load.
        setTimeout(() => window.URL.revokeObjectURL(url), 60000);
      } else {
        const anchor = window.document.createElement('a');
        anchor.href = url;
        anchor.download = filename || document.file_name;
        window.document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.URL.revokeObjectURL(url);
      }
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
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Documents</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Draft versions of your plan documents, prepared by your firm for review.
        </p>
      </div>

      <WatermarkNotice detail="These are watermarked drafts for review only — not legal documents. Nothing here is final until your attorney approves it." />

      {draftsEnabled && matterId && (
        <div>
          <Button asChild variant="outline">
            <Link href="/family/review">Review answers &amp; drafts</Link>
          </Button>
        </div>
      )}

      {mattersLoading && <LoadingState lines={3} label="Loading your matters…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={reloadMatters} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          variant="aurora"
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
        <section aria-label="Draft documents" className="space-y-3">
          {downloadError && (
            <p role="alert" className="text-sm text-lf-error-700">
              {downloadError}
            </p>
          )}
          {documents.length === 0 ? (
            <EmptyState
              variant="aurora"
              title="No documents yet"
              description="Your firm will share watermarked draft documents here once your plan takes shape."
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
                      <DocumentTypeTile fileName={document.file_name} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-lf-text">
                          {document.file_name}
                        </p>
                        <p className="text-xs text-lf-text-subtle">
                          {documentTypeLabel(document.file_name)} · Draft · v{document.version} ·{' '}
                          {formatDateTime(document.created_at)}
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
                        {downloadingId === document.id ? 'Preparing…' : 'Download draft'}
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
