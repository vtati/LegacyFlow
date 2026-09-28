'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Modal } from '@/components/ui/modal';
import { EmptyState, LoadingState } from '@/components/family/page-state';
import { AdminErrorState, FirmScopeNote } from '@/components/admin/admin-page-state';
import {
  adminApi,
  ApiError,
  type ContentPack,
  type PackImportResult,
} from '@/lib/api-client';
import { formatDate, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

/**
 * Valid next lifecycle states per current status, mirroring the API's
 * PACK_TRANSITIONS: draft → internal_testing → attorney_review → approved,
 * and internal_testing / attorney_review / approved → retired. Retired packs
 * have no forward moves.
 */
const VALID_TRANSITIONS: Record<string, string[]> = {
  draft: ['internal_testing'],
  internal_testing: ['attorney_review', 'retired'],
  attorney_review: ['approved', 'retired'],
  approved: ['retired'],
  retired: [],
};

function nextStates(status: string): string[] {
  return VALID_TRANSITIONS[status] ?? [];
}

interface TransitionTarget {
  pack: ContentPack;
  toStatus: string;
}

function TransitionConfirmModal({
  target,
  transitioning,
  error,
  onConfirm,
  onClose,
}: {
  target: TransitionTarget;
  transitioning: boolean;
  error: string | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const { pack, toStatus } = target;
  return (
    <Modal
      open
      onClose={onClose}
      title="Confirm lifecycle transition"
      description="This is recorded in your firm's audit log."
    >
      <div className="space-y-4">
        <p className="text-sm text-lf-text">
          Move <span className="font-semibold">{pack.display_name}</span>{' '}
          <span className="font-mono text-xs">(v{pack.version})</span> from{' '}
          <span className="font-medium">{formatStatus(pack.status)}</span> to{' '}
          <span className="font-medium">{formatStatus(toStatus)}</span>?
        </p>
        {toStatus === 'retired' && (
          <p className="text-sm text-lf-error-700">
            Retired packs cannot move forward again. Make sure no active matters depend on this
            pack version.
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-lf-error-700">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            className="min-h-[44px]"
            onClick={onClose}
            disabled={transitioning}
          >
            Cancel
          </Button>
          <Button
            type="button"
            className="min-h-[44px]"
            variant={toStatus === 'retired' ? 'destructive' : 'default'}
            onClick={onConfirm}
            disabled={transitioning}
          >
            {transitioning ? 'Moving…' : `Move to ${formatStatus(toStatus)}`}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function PackRow({
  pack,
  onTransition,
  transitioningKey,
}: {
  pack: ContentPack;
  onTransition: (pack: ContentPack, toStatus: string) => void;
  transitioningKey: string | null;
}) {
  const targets = nextStates(pack.status);
  return (
    <tr className="border-t border-lf-border">
      <td className="px-4 py-3">
        <p className="font-medium text-lf-text">{pack.display_name}</p>
        <p className="mt-0.5 font-mono text-xs text-lf-text-muted">{pack.pack_id}</p>
      </td>
      <td className="px-4 py-3 font-mono text-xs text-lf-text-muted">
        {pack.jurisdiction_code}
      </td>
      <td className="px-4 py-3 text-sm text-lf-text-muted">v{pack.version}</td>
      <td className="px-4 py-3">
        <span
          className={cn(
            'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
            statusBadgeClass(pack.status),
          )}
        >
          {formatStatus(pack.status)}
        </span>
      </td>
      <td className="px-4 py-3 text-sm text-lf-text-muted">
        {pack.effective_from ? formatDate(pack.effective_from) : '—'}
      </td>
      <td className="px-4 py-3">
        {targets.length === 0 ? (
          <span className="text-sm text-lf-text-subtle">No moves</span>
        ) : (
          <div className="flex flex-wrap gap-2">
            {targets.map((toStatus) => (
              <Button
                key={toStatus}
                type="button"
                variant="outline"
                size="sm"
                className="min-h-[44px] px-3"
                disabled={transitioningKey === pack.pack_id}
                onClick={() => onTransition(pack, toStatus)}
              >
                {transitioningKey === pack.pack_id ? 'Moving…' : formatStatus(toStatus)}
              </Button>
            ))}
          </div>
        )}
      </td>
    </tr>
  );
}

function ImportPackSection({ onImported }: { onImported: () => void }) {
  const [source, setSource] = useState<'dir' | 'bundle'>('bundle');
  const [packDir, setPackDir] = useState('');
  const [bundleFile, setBundleFile] = useState<File | null>(null);
  const [expectedHash, setExpectedHash] = useState('');
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PackImportResult | null>(null);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setResult(null);
    if (source === 'dir' && packDir.trim().length === 0) {
      setError('Enter the pack directory path, or switch to bundle upload.');
      return;
    }
    if (source === 'bundle' && !bundleFile) {
      setError('Choose a bundle .zip file to upload.');
      return;
    }
    setImporting(true);
    adminApi
      .importPack({
        packDir: source === 'dir' ? packDir : undefined,
        bundle: source === 'bundle' ? bundleFile ?? undefined : undefined,
        expectedBundleHash: expectedHash.trim() || undefined,
      })
      .then((imported) => {
        setResult(imported);
        setPackDir('');
        setBundleFile(null);
        setExpectedHash('');
        onImported();
      })
      .catch((err: unknown) =>
        setError(
          err instanceof ApiError && err.isPermissionDenied
            ? 'Importing packs requires the firm_admin role for your firm.'
            : err instanceof Error
              ? err.message
              : 'Could not import the pack.',
        ),
      )
      .finally(() => setImporting(false));
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Import a content pack</CardTitle>
        <CardDescription>
          Register a new pack version for your firm — either from a directory the API server can
          read, or by uploading a bundle .zip. The manifest is validated before anything is
          registered.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-5">
          <fieldset className="space-y-3">
            <legend className="text-sm font-medium text-lf-text">Import source</legend>
            <div className="flex flex-col gap-2 sm:flex-row">
              <label className="flex min-h-[44px] cursor-pointer items-center gap-2 rounded-lf-control border border-lf-border px-4 py-2 text-sm text-lf-text">
                <input
                  type="radio"
                  name="import-source"
                  checked={source === 'bundle'}
                  onChange={() => setSource('bundle')}
                  className="size-4 accent-lf-primary-700"
                />
                Bundle .zip upload
              </label>
              <label className="flex min-h-[44px] cursor-pointer items-center gap-2 rounded-lf-control border border-lf-border px-4 py-2 text-sm text-lf-text">
                <input
                  type="radio"
                  name="import-source"
                  checked={source === 'dir'}
                  onChange={() => setSource('dir')}
                  className="size-4 accent-lf-primary-700"
                />
                Server directory path
              </label>
            </div>
          </fieldset>

          {source === 'bundle' ? (
            <div className="space-y-1.5">
              <Label htmlFor="pack-bundle">Bundle file</Label>
              <Input
                id="pack-bundle"
                type="file"
                accept=".zip"
                onChange={(event) => setBundleFile(event.target.files?.[0] ?? null)}
                className="min-h-[44px] pt-2"
              />
              {bundleFile && (
                <p className="text-xs text-lf-text-muted">
                  Selected: {bundleFile.name} ({Math.round(bundleFile.size / 1024)} KB)
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-1.5">
              <Label htmlFor="pack-dir">Pack directory</Label>
              <Input
                id="pack-dir"
                value={packDir}
                onChange={(event) => setPackDir(event.target.value)}
                placeholder="e.g. /data/packs/us-va-1.2"
                autoComplete="off"
              />
              <p className="text-xs text-lf-text-muted">
                A directory on the API server containing a pack manifest.
              </p>
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="expected-hash">Expected bundle hash (optional)</Label>
            <Input
              id="expected-hash"
              value={expectedHash}
              onChange={(event) => setExpectedHash(event.target.value)}
              placeholder="SHA-256 of the bundle, when provided"
              autoComplete="off"
              className="font-mono text-xs"
            />
            <p className="text-xs text-lf-text-muted">
              When supplied, the uploaded bundle is verified against this hash before import.
            </p>
          </div>

          {error && (
            <p role="alert" className="text-sm text-lf-error-700">
              {error}
            </p>
          )}
          {result && (
            <div
              role="status"
              className="rounded-lf-control border border-lf-success-200 bg-lf-success-50 px-4 py-3 text-sm text-lf-success-700"
            >
              Imported <span className="font-semibold">{result.pack_id}</span> v{result.version}{' '}
              for <span className="font-mono text-xs">{result.jurisdiction_code}</span> —
              status {formatStatus(result.status)}.
            </div>
          )}

          <Button type="submit" className="min-h-[44px]" disabled={importing}>
            {importing ? 'Importing…' : 'Import pack'}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

/** Admin → Content Packs: lifecycle table with valid transitions plus pack import. */
export default function AdminContentPacksPage(): React.JSX.Element {
  const [packs, setPacks] = useState<ContentPack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [target, setTarget] = useState<TransitionTarget | null>(null);
  const [transitioning, setTransitioning] = useState(false);
  const [transitionError, setTransitionError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    adminApi
      .listPacks()
      .then((result) => setPacks([...result]))
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleConfirmTransition = () => {
    if (!target) return;
    setTransitioning(true);
    setTransitionError(null);
    adminApi
      .transitionPack(target.pack.pack_id, target.toStatus)
      .then(() => {
        setTarget(null);
        load();
      })
      .catch((err: unknown) =>
        setTransitionError(
          err instanceof Error ? err.message : 'Could not transition the pack.',
        ),
      )
      .finally(() => setTransitioning(false));
  };

  const sorted = [...packs].sort(
    (a, b) =>
      a.jurisdiction_code.localeCompare(b.jurisdiction_code) ||
      b.version.localeCompare(a.version),
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Content Packs</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          Draft, review, approve, and retire the content packs your firm relies on.
        </p>
      </div>

      <FirmScopeNote>
        Pack lifecycle changes here apply to your firm and are written to your firm&apos;s
        audit log.
      </FirmScopeNote>

      {loading && <LoadingState lines={5} label="Loading content packs…" />}

      {error != null && !loading ? <AdminErrorState error={error} onRetry={load} /> : null}

      {!loading && !error && sorted.length === 0 && (
        <EmptyState
          title="No content packs yet"
          description="Import your firm's first pack below — it starts in draft and moves through testing and attorney review before approval."
        />
      )}

      {!loading && !error && sorted.length > 0 && (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <caption className="sr-only">Content packs and lifecycle actions</caption>
              <thead>
                <tr className="border-b border-lf-border text-xs uppercase tracking-wide text-lf-text-muted">
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Pack
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Jurisdiction
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Version
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Status
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Effective
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Lifecycle
                  </th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((pack) => (
                  <PackRow
                    key={`${pack.pack_id}:${pack.version}`}
                    pack={pack}
                    onTransition={(p, toStatus) => {
                      setTransitionError(null);
                      setTarget({ pack: p, toStatus });
                    }}
                    transitioningKey={transitioning && target ? target.pack.pack_id : null}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {!loading && !error && <ImportPackSection onImported={load} />}

      {target && (
        <TransitionConfirmModal
          target={target}
          transitioning={transitioning}
          error={transitionError}
          onConfirm={handleConfirmTransition}
          onClose={() => {
            if (!transitioning) setTarget(null);
          }}
        />
      )}
    </div>
  );
}
