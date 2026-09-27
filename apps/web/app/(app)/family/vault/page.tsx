'use client';

import { useCallback, useEffect, useState } from 'react';
import { Check, Copy, Link2, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { MatterSelect, useMatters } from '@/components/family/matter-context';
import { api, type VaultShare, type VaultShareCreated } from '@/lib/api-client';
import { formatDate, formatDateTime } from '@/lib/format';
import { cn } from '@/lib/utils';

function ShareStatus({ share }: { share: VaultShare }) {
  const revoked = share.revoked_at != null;
  return (
    <span
      className={cn(
        'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
        revoked ? 'bg-lf-error-100 text-lf-error-700' : 'bg-lf-success-100 text-lf-success-700',
      )}
    >
      {revoked ? 'Revoked' : 'Active'}
    </span>
  );
}

interface ShareRowProps {
  share: VaultShare;
  revoking: boolean;
  onRevoke: (share: VaultShare) => void;
}

function ShareRow({ share, revoking, onRevoke }: ShareRowProps) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-lf-text">{share.label}</p>
        <p className="text-xs text-lf-text-subtle">
          Created {formatDateTime(share.created_at)}
          {share.expires_at ? ` · expires ${formatDate(share.expires_at)}` : ' · no expiry'}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <ShareStatus share={share} />
        {share.revoked_at == null && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={revoking}
            onClick={() => onRevoke(share)}
            className="text-lf-error-700 hover:text-lf-error-700"
          >
            <Trash2 className="size-4" aria-hidden="true" />
            {revoking ? 'Revoking…' : 'Revoke'}
          </Button>
        )}
      </div>
    </li>
  );
}

function TokenReveal({
  share,
  onDismiss,
}: {
  share: VaultShareCreated;
  onDismiss: () => void;
}): React.JSX.Element {
  const [copied, setCopied] = useState(false);
  const link = `${window.location.origin}/vault/${share.token}`;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Card className="border-lf-accent-600" aria-label="New vault share">
      <CardHeader>
        <CardTitle>Share created — save this link now</CardTitle>
        <CardDescription>
          This link is shown exactly once. Anyone with it can see the redacted summary — share it
          only with someone you trust.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2 rounded-lf-control bg-lf-canvas-subtle px-3 py-2.5">
          <Link2 className="size-4 shrink-0 text-lf-text-subtle" aria-hidden="true" />
          <code className="min-w-0 flex-1 break-all text-sm text-lf-text">{link}</code>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" size="sm" onClick={handleCopy}>
            {copied ? (
              <Check className="size-4" aria-hidden="true" />
            ) : (
              <Copy className="size-4" aria-hidden="true" />
            )}
            {copied ? 'Copied' : 'Copy link'}
          </Button>
          <Button type="button" size="sm" onClick={onDismiss}>
            I’ve saved it
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** Family vault — share list, share creation (token shown once), revocation. */
export default function FamilyVaultPage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [matterId, setMatterId] = useState<string | null>(null);
  const [shares, setShares] = useState<VaultShare[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [label, setLabel] = useState('');
  const [expiry, setExpiry] = useState('');
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [created, setCreated] = useState<VaultShareCreated | null>(null);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadShares = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    api
      .listShares(id)
      .then(setShares)
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!matterId && matters.length > 0) {
      setMatterId(matters[0]?.id ?? null);
    }
  }, [matters, matterId]);

  useEffect(() => {
    if (matterId) {
      loadShares(matterId);
      setCreated(null);
    }
  }, [matterId, loadShares]);

  const handleCreate = (event: React.FormEvent) => {
    event.preventDefault();
    if (!matterId) return;
    if (label.trim().length === 0) {
      setFormError('Give the share a label so you remember who it is for.');
      return;
    }
    setCreating(true);
    setFormError(null);
    setNotice(null);
    api
      .createShare(matterId, {
        label: label.trim(),
        expires_at: expiry ? new Date(`${expiry}T23:59:59`).toISOString() : null,
      })
      .then((share) => {
        setCreated(share);
        setShares((prev) => [share, ...prev.filter((s) => s.id !== share.id)]);
        setLabel('');
        setExpiry('');
      })
      .catch((err: unknown) =>
        setFormError(err instanceof Error ? err.message : 'Could not create the share.'),
      )
      .finally(() => setCreating(false));
  };

  const handleRevoke = (share: VaultShare) => {
    const confirmed = window.confirm(
      `Revoke “${share.label}”? The link will stop working immediately.`,
    );
    if (!confirmed) return;
    setRevokingId(share.id);
    setNotice(null);
    api
      .revokeShare(share.id)
      .then((revoked) => {
        setShares((prev) => prev.map((s) => (s.id === revoked.id ? revoked : s)));
        setNotice(`“${share.label}” revoked — the link no longer works.`);
      })
      .catch((err: unknown) =>
        setNotice(err instanceof Error ? `Could not revoke: ${err.message}` : 'Could not revoke.'),
      )
      .finally(() => setRevokingId(null));
  };

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Vault</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          Share a redacted summary of your plan with someone you trust — a link that shows the
          highlights, never the full documents. Revoke it any time.
        </p>
      </div>

      {mattersLoading && <LoadingState lines={3} label="Loading your matters…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={reloadMatters} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your vault shares will appear here once your firm starts a plan for your household."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && (
        <MatterSelect matters={matters} value={matterId} onChange={setMatterId} />
      )}

      {matterId && loading && <LoadingState lines={4} label="Loading shares…" />}
      {error != null && matterId != null && !loading ? (
        <ErrorState error={error} onRetry={() => loadShares(matterId)} />
      ) : null}

      {created && <TokenReveal share={created} onDismiss={() => setCreated(null)} />}

      {notice && (
        <p
          role="status"
          className="rounded-lf-control border border-lf-success-600 bg-lf-success-50 px-4 py-3 text-sm text-lf-success-700"
        >
          {notice}
        </p>
      )}

      {matterId && !loading && !error && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Create a share</CardTitle>
              <CardDescription>
                The recipient sees a redacted summary — no account needed, no full documents.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleCreate} className="flex flex-wrap items-end gap-3">
                <div className="min-w-52 flex-1 space-y-1.5">
                  <Label htmlFor="share-label">Label</Label>
                  <input
                    id="share-label"
                    type="text"
                    value={label}
                    onChange={(event) => setLabel(event.target.value)}
                    placeholder="e.g. For my sister Ana"
                    className="h-10 w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="share-expiry">Expires (optional)</Label>
                  <input
                    id="share-expiry"
                    type="date"
                    value={expiry}
                    onChange={(event) => setExpiry(event.target.value)}
                    className="h-10 rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
                  />
                </div>
                <Button type="submit" disabled={creating}>
                  {creating ? 'Creating…' : 'Create share'}
                </Button>
              </form>
              {formError && (
                <p role="alert" className="mt-3 text-sm text-lf-error-700">
                  {formError}
                </p>
              )}
            </CardContent>
          </Card>

          <section aria-label="Vault shares" className="space-y-3">
            <h2 className="text-base font-semibold text-lf-text">Shares</h2>
            {shares.length === 0 ? (
              <EmptyState
                title="No shares yet"
                description="Create a share above to give someone a read-only, redacted view of your plan."
              />
            ) : (
              <Card>
                <CardContent className="p-0">
                  <ul className="divide-y divide-lf-border">
                    {shares.map((share) => (
                      <ShareRow
                        key={share.id}
                        share={share}
                        revoking={revokingId === share.id}
                        onRevoke={handleRevoke}
                      />
                    ))}
                  </ul>
                </CardContent>
              </Card>
            )}
          </section>
        </>
      )}
    </div>
  );
}
