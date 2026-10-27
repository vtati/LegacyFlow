'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { AttorneyReviewSection } from '@/components/firm/attorney-review-section';
import { firmApi, type ReviewPacketDetail, type ReviewPacketSummary } from '@/lib/api-client';
import { formatDateTime } from '@/lib/format';

interface SnapshotSection {
  title?: unknown;
  items?: Array<{ label?: unknown; value?: unknown }> | unknown;
}

function isSnapshotSection(value: unknown): value is SnapshotSection {
  return typeof value === 'object' && value !== null && 'items' in value;
}

function stringify(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

/** Render the snapshot payload: structured sections when present, raw JSON otherwise. */
function SnapshotViewer({ snapshot }: { snapshot: Record<string, unknown> }) {
  const sections = Array.isArray(snapshot.sections)
    ? snapshot.sections.filter(isSnapshotSection)
    : [];

  if (sections.length > 0) {
    return (
      <div className="space-y-6">
        {sections.map((section, index) => (
          <section key={index} aria-label={String(section.title ?? `Section ${index + 1}`)}>
            <h3 className="text-sm font-semibold text-lf-text">
              {String(section.title ?? 'Details')}
            </h3>
            <dl className="mt-2 divide-y divide-lf-border rounded-lf-control border border-lf-border">
              {(Array.isArray(section.items) ? section.items : []).map((item, itemIndex) => (
                <div key={itemIndex} className="grid gap-1 px-4 py-2.5 sm:grid-cols-3 sm:gap-3">
                  <dt className="text-sm font-medium text-lf-text-muted">
                    {stringify(item.label)}
                  </dt>
                  <dd className="text-sm text-lf-text sm:col-span-2">{stringify(item.value)}</dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    );
  }

  return (
    <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-lf-control bg-lf-canvas-subtle p-4 text-xs leading-relaxed text-lf-text">
      {JSON.stringify(snapshot, null, 2)}
    </pre>
  );
}

/** Review-packet tab: version selector, generate button, snapshot viewer. */
export function ReviewPacketTab({ matterId }: { matterId: string }): React.JSX.Element {
  const [packets, setPackets] = useState<ReviewPacketSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [packet, setPacket] = useState<ReviewPacketDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingPacket, setLoadingPacket] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadPackets = useCallback(() => {
    setLoading(true);
    setError(null);
    firmApi
      .listReviewPackets(matterId)
      .then((result) => {
        const sorted = [...result].sort((a, b) => b.version - a.version);
        setPackets(sorted);
        if (sorted.length > 0 && !selectedId) {
          setSelectedId(sorted[0]?.id ?? null);
        }
      })
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, [matterId, selectedId]);

  useEffect(() => {
    loadPackets();
  }, [loadPackets]);

  useEffect(() => {
    if (!selectedId) {
      setPacket(null);
      return;
    }
    setLoadingPacket(true);
    firmApi
      .getReviewPacket(matterId, selectedId)
      .then(setPacket)
      .catch((err: unknown) => setError(err))
      .finally(() => setLoadingPacket(false));
  }, [matterId, selectedId]);

  const handleGenerate = () => {
    setGenerating(true);
    setNotice(null);
    firmApi
      .generateReviewPacket(matterId)
      .then((result) => {
        setNotice(`Version ${result.version} generated.`);
        setPackets((prev) => [result, ...prev.filter((p) => p.id !== result.id)]);
        setSelectedId(result.id);
      })
      .catch((err: unknown) =>
        setNotice(err instanceof Error ? err.message : 'Could not generate the packet.'),
      )
      .finally(() => setGenerating(false));
  };

  if (loading) return <LoadingState lines={4} label="Loading review packets…" />;
  if (error != null) return <ErrorState error={error} onRetry={loadPackets} />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-lf-text-muted">
          Review packets capture a point-in-time snapshot of the matter for attorney review.
        </p>
        <Button type="button" onClick={handleGenerate} disabled={generating}>
          {generating ? 'Generating…' : 'Generate review packet'}
        </Button>
      </div>

      {notice && (
        <p role="status" className="text-sm text-lf-text-muted">
          {notice}
        </p>
      )}

      {packets.length === 0 ? (
        <EmptyState
          title="No review packets yet"
          description="Generate a review packet to capture the current state of this matter for review."
        />
      ) : (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <CardTitle>Version {packet?.version ?? packets[0]?.version}</CardTitle>
                <CardDescription>
                  {packet
                    ? `Generated ${formatDateTime(packet.created_at)}`
                    : 'Select a version to inspect'}
                </CardDescription>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="packet-version">Packet version</Label>
                <select
                  id="packet-version"
                  value={selectedId ?? ''}
                  onChange={(event) => setSelectedId(event.target.value)}
                  className="h-10 rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
                >
                  {packets.map((p) => (
                    <option key={p.id} value={p.id}>
                      Version {p.version} — {formatDateTime(p.created_at)}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {loadingPacket ? (
              <LoadingState lines={3} label="Loading packet…" />
            ) : packet ? (
              <SnapshotViewer snapshot={packet.snapshot} />
            ) : (
              <p className="text-sm text-lf-text-muted">Choose a packet version above.</p>
            )}
          </CardContent>
        </Card>
      )}

      <AttorneyReviewSection matterId={matterId} />
    </div>
  );
}
