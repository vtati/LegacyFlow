'use client';

import { useCallback, useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, LoadingState } from '@/components/family/page-state';
import { AdminErrorState, FirmScopeNote } from '@/components/admin/admin-page-state';
import { adminApi, createFirm, type FirmMember, type FirmProfile } from '@/lib/api-client';
import { formatDate, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

/**
 * Responsive table cells (presentation only): below the md breakpoint each
 * table row becomes a stacked label/value card. Labels come from the cell's
 * data-th attribute. Same table, same data, no behavior change.
 */
const MOBILE_TABLE_CELL =
  'max-md:block max-md:border-0 max-md:px-0 max-md:py-1.5 max-md:before:mb-1 max-md:before:block max-md:before:text-[11px] max-md:before:font-semibold max-md:before:uppercase max-md:before:tracking-wider max-md:before:text-lf-text-subtle max-md:before:content-[attr(data-th)]';

const ROLE_ORDER = ['firm_admin', 'attorney', 'staff', 'auditor'] as const;

function FirmProfileCard({ firm }: { firm: FirmProfile }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{firm.display_name}</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-muted">
              Legal name
            </dt>
            <dd className="mt-1 text-sm text-lf-text">{firm.legal_name}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-muted">
              Display name
            </dt>
            <dd className="mt-1 text-sm text-lf-text">{firm.display_name}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-muted">
              Status
            </dt>
            <dd className="mt-1">
              <span
                className={cn(
                  'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
                  statusBadgeClass(firm.status),
                )}
              >
                {formatStatus(firm.status)}
              </span>
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-muted">
              Created
            </dt>
            <dd className="mt-1 text-sm text-lf-text">{formatDate(firm.created_at)}</dd>
          </div>
        </dl>
      </CardContent>
    </Card>
  );
}

function MemberRow({ member }: { member: FirmMember }) {
  return (
    <tr className="border-t border-lf-border transition-colors hover:bg-lf-canvas-subtle max-md:block max-md:rounded-lf-card max-md:border max-md:border-lf-border max-md:bg-white max-md:p-4">
      <td data-th="Name / email" className={`px-4 py-3 ${MOBILE_TABLE_CELL}`}>
        <p className="font-medium text-lf-text">{member.display_name ?? '—'}</p>
        <p className="mt-0.5 text-sm text-lf-text-muted">{member.email ?? '—'}</p>
      </td>
      <td data-th="Role" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>{formatStatus(member.role)}</td>
      <td data-th="Status" className={`px-4 py-3 ${MOBILE_TABLE_CELL}`}>
        <span
          className={cn(
            'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
            statusBadgeClass(member.status),
          )}
        >
          {formatStatus(member.status)}
        </span>
      </td>
      <td data-th="Joined" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>{formatDate(member.created_at)}</td>
    </tr>
  );
}

/** Admin → Firms: the caller's own firm profile plus its member roster. */
export default function AdminFirmsPage(): React.JSX.Element {
  const [firm, setFirm] = useState<FirmProfile | null>(null);
  const [members, setMembers] = useState<FirmMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([adminApi.getCurrentFirm(), adminApi.listFirmMembers()])
      .then(([profile, roster]) => {
        setFirm(profile);
        const rank = (role: string) => {
          const index = (ROLE_ORDER as readonly string[]).indexOf(role);
          return index === -1 ? ROLE_ORDER.length : index;
        };
        setMembers(
          [...roster].sort(
            (a, b) =>
              rank(a.role) - rank(b.role) ||
              (a.display_name ?? a.email ?? '').localeCompare(b.display_name ?? b.email ?? ''),
          ),
        );
      })
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Firms</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Your firm&apos;s profile and who has access to it.
        </p>
      </div>

      <FirmScopeNote>
        Admin endpoints are scoped to the caller&apos;s firm — this shows your firm only.
      </FirmScopeNote>

      {loading && <LoadingState lines={5} label="Loading firm…" />}

      {error != null && !loading ? <AdminErrorState error={error} onRetry={load} /> : null}

      {!loading && !error && firm && (
        <>
          <section aria-label="Firm profile">
            <FirmProfileCard firm={firm} />
          </section>

          <section aria-label="Member roster" className="space-y-3">
            <h2 className="text-lg font-semibold text-lf-text">
              Members{' '}
              <span className="text-sm font-normal text-lf-text-muted">
                ({members.length})
              </span>
            </h2>
            {members.length === 0 ? (
              <EmptyState
                title="No members"
                description="Your firm has no member records yet. Invitations appear here once sent."
              />
            ) : (
              <Card>
                <div className="overflow-x-auto md:max-h-[75vh] md:overflow-y-auto">
                  <table className="w-full text-left text-sm tabular-nums md:min-w-[560px]">
                    <caption className="sr-only">Firm member roster</caption>
                    <thead className="max-md:hidden md:sticky md:top-0 md:bg-white">
                      <tr className="border-b border-lf-border text-xs uppercase tracking-wide text-lf-text-muted">
                        <th scope="col" className="px-4 py-3 font-semibold">
                          Name / email
                        </th>
                        <th scope="col" className="px-4 py-3 font-semibold">
                          Role
                        </th>
                        <th scope="col" className="px-4 py-3 font-semibold">
                          Status
                        </th>
                        <th scope="col" className="px-4 py-3 font-semibold">
                          Joined
                        </th>
                      </tr>
                    </thead>
                    <tbody className="max-md:block max-md:space-y-3 max-md:bg-lf-canvas-subtle max-md:p-4">
                      {members.map((member) => (
                        <MemberRow key={member.id} member={member} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </section>

          <section aria-label="Create new firm" className="space-y-3">
            <h2 className="text-lg font-semibold text-lf-text">Create a new firm</h2>
            <Card>
              <CardContent className="space-y-4 pt-6">
                <p className="text-sm text-lf-text-muted">
                  Create a new firm. You will become its administrator.
                </p>
                <CreateFirmForm onCreated={load} />
              </CardContent>
            </Card>
          </section>
        </>
      )}
    </div>
  );
}

function CreateFirmForm({ onCreated }: { onCreated: () => void }): React.JSX.Element {
  const [legalName, setLegalName] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await createFirm({ legal_name: legalName.trim(), display_name: displayName.trim() });
      setLegalName('');
      setDisplayName('');
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create firm.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label htmlFor="firm-legal-name" className="block text-sm font-medium text-lf-text">
          Legal name
        </label>
        <input
          id="firm-legal-name"
          type="text"
          required
          value={legalName}
          onChange={(e) => setLegalName(e.target.value)}
          placeholder="Gen Alpha Labs LLC"
          className="mt-1 block w-full rounded-md border border-lf-border bg-lf-canvas px-3 py-2 text-sm text-lf-text"
        />
      </div>
      <div>
        <label htmlFor="firm-display-name" className="block text-sm font-medium text-lf-text">
          Display name
        </label>
        <input
          id="firm-display-name"
          type="text"
          required
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="Gen Alpha Labs"
          className="mt-1 block w-full rounded-md border border-lf-border bg-lf-canvas px-3 py-2 text-sm text-lf-text"
        />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button
        type="submit"
        disabled={saving || !legalName.trim() || !displayName.trim()}
        className="rounded-md bg-lf-primary px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
      >
        {saving ? 'Creating…' : 'Create firm'}
      </button>
    </form>
  );
}
