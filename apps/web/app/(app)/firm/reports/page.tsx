'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { firmApi, type MatterSummary } from '@/lib/api-client';
import { fetchAllMatters, normalizeStatus } from '@/lib/firm-matters';
import { formatStatus } from '@/lib/format';

/**
 * Responsive table cells (presentation only): below the md breakpoint each
 * table row becomes a stacked label/value card. Labels come from the cell's
 * data-th attribute. Same table, same data, no behavior change.
 */
const MOBILE_TABLE_CELL =
  'max-md:block max-md:border-0 max-md:px-0 max-md:py-1.5 max-md:before:mb-1 max-md:before:block max-md:before:text-[11px] max-md:before:font-semibold max-md:before:uppercase max-md:before:tracking-wider max-md:before:text-lf-text-subtle max-md:before:content-[attr(data-th)]';

interface ReportData {
  matters: MatterSummary[];
  openClarifications: number;
  pendingGates: number;
  detailsLoaded: boolean;
}

function toCsvRow(values: (string | number)[]): string {
  return values
    .map((value) => {
      const text = String(value);
      return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    })
    .join(',');
}

function downloadCsv(filename: string, rows: (string | number)[][]) {
  const content = rows.map(toCsvRow).join('\r\n');
  const blob = new Blob([content], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Firm reports: pipeline funnel, completeness, jurisdiction mix, CSV export. */
export default function FirmReportsPage(): React.JSX.Element {
  const [data, setData] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchAllMatters()
      .then((matters) =>
        Promise.allSettled(
          matters.map((matter) =>
            Promise.all([firmApi.listClarifications(matter.id), firmApi.listGates(matter.id)]),
          ),
        ).then((settled) => {
          let openClarifications = 0;
          let pendingGates = 0;
          let detailsLoaded = true;
          for (const result of settled) {
            if (result.status === 'fulfilled') {
              openClarifications += result.value[0].filter((c) => c.status === 'open').length;
              pendingGates += result.value[1].filter((g) => g.status === 'pending').length;
            } else {
              detailsLoaded = false;
            }
          }
          setData({ matters, openClarifications, pendingGates, detailsLoaded });
        }),
      )
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const stats = useMemo(() => {
    if (!data) return null;
    const byStatus = new Map<string, number>();
    const byJurisdiction = new Map<string, number>();
    let completenessSum = 0;
    for (const matter of data.matters) {
      const status = normalizeStatus(matter.status);
      byStatus.set(status, (byStatus.get(status) ?? 0) + 1);
      byJurisdiction.set(matter.jurisdiction_code, (byJurisdiction.get(matter.jurisdiction_code) ?? 0) + 1);
      completenessSum += matter.completeness_pct;
    }
    const total = data.matters.length;
    const maxFunnel = Math.max(1, ...byStatus.values());
    return {
      total,
      avgCompleteness: total === 0 ? 0 : completenessSum / total,
      funnel: [...byStatus.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([status, count]) => ({ status, count, pct: Math.round((count / maxFunnel) * 100) })),
      jurisdictions: [...byJurisdiction.entries()].sort((a, b) => b[1] - a[1]),
    };
  }, [data]);

  if (loading) return <LoadingState lines={6} label="Loading reports…" />;
  if (error != null) return <ErrorState error={error} onRetry={load} />;
  if (!data || !stats) return <LoadingState lines={4} label="Computing reports…" />;

  const handleExport = () => {
    const rows: (string | number)[][] = [
      ['Matter', 'Household', 'Status', 'Jurisdiction', 'Completeness %', 'Updated'],
      ...data.matters.map((m) => [
        m.title || '(untitled matter)',
        m.household?.name ?? '',
        m.status,
        m.jurisdiction_code,
        m.completeness_pct,
        m.updated_at,
      ]),
      [],
      ['Pipeline status', 'Count'],
      ...stats.funnel.map((f) => [formatStatus(f.status), f.count]),
      [],
      ['Jurisdiction', 'Count'],
      ...stats.jurisdictions.map(([code, count]) => [code, count]),
      [],
      ['Metric', 'Value'],
      ['Average completeness %', Math.round(stats.avgCompleteness)],
      ['Open clarifications', data.openClarifications],
      ['Pending gates', data.pendingGates],
    ];
    const stamp = new Date().toISOString().slice(0, 10);
    downloadCsv(`legacyflow-firm-report-${stamp}.csv`, rows);
  };

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Reports</h1>
          <p className="mt-2 text-sm text-lf-text-muted">
            Operational metrics computed from the firm&rsquo;s matters.
          </p>
        </div>
        <Button type="button" onClick={handleExport} className="min-h-[44px]">
          Export CSV
        </Button>
      </div>

      {stats.total === 0 ? (
        <EmptyState
          title="No matters to report"
          description="Reports appear here once the firm has matters."
        />
      ) : (
        <>
          <section aria-label="Totals" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {[
              { label: 'Total matters', value: stats.total },
              { label: 'Average completeness', value: `${Math.round(stats.avgCompleteness)}%` },
              { label: 'Open clarifications', value: data.openClarifications },
              { label: 'Pending gates', value: data.pendingGates },
            ].map((card) => (
              <Card key={card.label}>
                <CardContent className="p-6">
                  <p className="text-3xl font-bold tabular-nums text-lf-text">{card.value}</p>
                  <p className="mt-1 text-sm text-lf-text-muted">{card.label}</p>
                </CardContent>
              </Card>
            ))}
          </section>
          {!data.detailsLoaded && (
            <p role="note" className="text-sm text-lf-text-muted">
              Clarification and gate totals are partial — some matters could not be read.
            </p>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Pipeline funnel</CardTitle>
              <CardDescription>Matters by lifecycle status.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {stats.funnel.map((stage) => (
                <div key={stage.status} className="space-y-1">
                  <div className="flex items-baseline justify-between text-sm">
                    <span className="font-medium text-lf-text">{formatStatus(stage.status)}</span>
                    <span className="text-lf-text-muted">{stage.count}</span>
                  </div>
                  <div
                    role="img"
                    aria-label={`${formatStatus(stage.status)}: ${stage.count} matters`}
                    className="h-2.5 overflow-hidden rounded-full bg-lf-canvas-subtle"
                  >
                    <div
                      className="h-full rounded-full bg-lf-primary-500"
                      style={{ width: `${stage.pct}%` }}
                    />
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Matters by jurisdiction</CardTitle>
              <CardDescription>Where the firm&rsquo;s matters sit.</CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto md:max-h-[75vh] md:overflow-y-auto">
                <table className="w-full text-left text-sm tabular-nums md:min-w-[320px]">
                  <caption className="sr-only">Matters by jurisdiction</caption>
                  <thead className="max-md:hidden md:sticky md:top-0 md:bg-white">
                    <tr className="border-b border-lf-border text-xs uppercase tracking-wide text-lf-text-muted">
                      <th scope="col" className="px-4 py-3 font-semibold">
                        Jurisdiction
                      </th>
                      <th scope="col" className="px-4 py-3 font-semibold">
                        Matters
                      </th>
                    </tr>
                  </thead>
                  <tbody className="max-md:block max-md:space-y-3 max-md:bg-lf-canvas-subtle max-md:p-4">
                    {stats.jurisdictions.map(([code, count]) => (
                      <tr key={code} className="border-t border-lf-border transition-colors hover:bg-lf-canvas-subtle max-md:block max-md:rounded-lf-card max-md:border max-md:border-lf-border max-md:bg-white max-md:p-4">
                        <td data-th="Jurisdiction" className={`px-4 py-3 font-medium text-lf-text ${MOBILE_TABLE_CELL}`}>{code}</td>
                        <td data-th="Matters" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>{count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
