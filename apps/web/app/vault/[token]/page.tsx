'use client';

import { use, useEffect, useState } from 'react';
import { EyeOff, FileText, ShieldCheck } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { api, type VaultAccessSummary } from '@/lib/api-client';
import { formatDate } from '@/lib/format';

interface VaultAccessPageProps {
  params: Promise<{ token: string }>;
}

/**
 * Public vault access page — no authentication. Shows only the redacted
 * summary for a valid share token; invalid or revoked tokens get a generic
 * not-found state.
 */
export default function VaultAccessPage({ params }: VaultAccessPageProps): React.JSX.Element {
  const { token } = use(params);
  const [summary, setSummary] = useState<VaultAccessSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [invalid, setInvalid] = useState(false);

  useEffect(() => {
    setLoading(true);
    setInvalid(false);
    api
      .accessByToken(token)
      .then(setSummary)
      .catch(() => {
        setSummary(null);
        setInvalid(true);
      })
      .finally(() => setLoading(false));
  }, [token]);

  return (
    <main
      id="main-content"
      className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center px-4 py-12"
    >
      {loading && (
        <div role="status" aria-label="Loading shared summary" className="space-y-3">
          <div className="h-8 w-1/2 animate-pulse rounded-lf-card bg-lf-canvas-subtle" />
          <div className="h-40 w-full animate-pulse rounded-lf-card bg-lf-canvas-subtle" />
          <span className="sr-only">Loading…</span>
        </div>
      )}

      {!loading && (invalid || summary === null) && (
        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <span className="flex size-10 items-center justify-center rounded-lf-control bg-lf-accent-100 text-lf-accent-800">
                <EyeOff className="size-5" aria-hidden="true" />
              </span>
              <div>
                <CardTitle>This link doesn’t work anymore</CardTitle>
                <CardDescription>
                  It may have expired, been revoked, or been typed incorrectly.
                </CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-lf-text-muted">
              Ask the person who shared it with you for a fresh link. No account is needed to view a
              valid share.
            </p>
          </CardContent>
        </Card>
      )}

      {!loading && !invalid && summary !== null && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center gap-3">
                <span className="flex size-10 items-center justify-center rounded-lf-control bg-lf-primary-50 text-lf-primary-800">
                  <ShieldCheck className="size-5" aria-hidden="true" />
                </span>
                <div>
                  <CardTitle>{summary.matter_title}</CardTitle>
                  <CardDescription>
                    Shared as “{summary.share_label}” · {summary.jurisdiction_code}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="rounded-lf-control bg-lf-canvas-subtle px-4 py-3 text-sm text-lf-text-muted">
                This is a <strong className="text-lf-text">redacted summary</strong> — the
                highlights of an estate plan, shared for information only. Full documents are never
                shown here.
              </p>
              <div>
                <h2 className="text-sm font-semibold text-lf-text">Documents in this plan</h2>
                {summary.documents.length === 0 ? (
                  <p className="mt-1 text-sm text-lf-text-muted">
                    No documents are included in this summary.
                  </p>
                ) : (
                  <ul className="mt-2 space-y-2">
                    {summary.documents.map((document, index) => (
                      <li key={index} className="flex items-center gap-3 text-sm text-lf-text">
                        <FileText
                          className="size-4 shrink-0 text-lf-text-subtle"
                          aria-hidden="true"
                        />
                        <span className="truncate">{document.filename}</span>
                        <span className="shrink-0 text-xs text-lf-text-subtle">
                          {document.content_type}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              {summary.expires_at && (
                <p className="text-xs text-lf-text-subtle">
                  This link expires {formatDate(summary.expires_at)}.
                </p>
              )}
            </CardContent>
          </Card>
          <p className="text-center text-xs text-lf-text-subtle">
            Educational information only, not legal advice.
          </p>
        </div>
      )}
    </main>
  );
}
