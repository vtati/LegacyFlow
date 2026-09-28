'use client';

import { useCallback, useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, LoadingState } from '@/components/family/page-state';
import { AdminErrorState, FirmScopeNote } from '@/components/admin/admin-page-state';
import { adminApi, type FirmMember, type FirmProfile } from '@/lib/api-client';
import { formatDate, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

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
    <tr className="border-t border-lf-border">
      <td className="px-4 py-3">
        <p className="font-medium text-lf-text">{member.display_name ?? '—'}</p>
        <p className="mt-0.5 text-sm text-lf-text-muted">{member.email ?? '—'}</p>
      </td>
      <td className="px-4 py-3 text-sm text-lf-text-muted">{formatStatus(member.role)}</td>
      <td className="px-4 py-3">
        <span
          className={cn(
            'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
            statusBadgeClass(member.status),
          )}
        >
          {formatStatus(member.status)}
        </span>
      </td>
      <td className="px-4 py-3 text-sm text-lf-text-muted">{formatDate(member.created_at)}</td>
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
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Firms</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
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
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[560px] text-left text-sm">
                    <caption className="sr-only">Firm member roster</caption>
                    <thead>
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
                    <tbody>
                      {members.map((member) => (
                        <MemberRow key={member.id} member={member} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </section>
        </>
      )}
    </div>
  );
}
