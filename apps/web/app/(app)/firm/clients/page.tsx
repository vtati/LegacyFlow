'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { ApiError, firmApi, type Invitation, type MatterSummary } from '@/lib/api-client';
import { fetchAllMatters } from '@/lib/firm-matters';
import { formatDateTime, formatStatus } from '@/lib/format';

/**
 * Responsive table cells (presentation only): below the md breakpoint each
 * table row becomes a stacked label/value card. Labels come from the cell's
 * data-th attribute. Same table, same data, no behavior change.
 */
const MOBILE_TABLE_CELL =
  'max-md:block max-md:border-0 max-md:px-0 max-md:py-1.5 max-md:before:mb-1 max-md:before:block max-md:before:text-[11px] max-md:before:font-semibold max-md:before:uppercase max-md:before:tracking-wider max-md:before:text-lf-text-subtle max-md:before:content-[attr(data-th)]';
const MOBILE_TABLE_ACTION_CELL = 'max-md:block max-md:border-0 max-md:px-0 max-md:pt-3';

const INVITATION_ROLES = [
  { value: 'client_primary', label: 'Client (primary)' },
  { value: 'client_collaborator', label: 'Client (collaborator)' },
  { value: 'firm_attorney', label: 'Attorney' },
  { value: 'firm_staff', label: 'Staff' },
  { value: 'firm_auditor', label: 'Auditor' },
  { value: 'firm_admin', label: 'Firm admin' },
] as const;

interface HouseholdGroup {
  id: string;
  name: string;
  matters: MatterSummary[];
}

function groupByHousehold(matters: MatterSummary[]): HouseholdGroup[] {
  const groups = new Map<string, HouseholdGroup>();
  for (const matter of matters) {
    const hid = matter.household?.id;
    if (!hid) continue; // matter without a linked household: not groupable
    const existing = groups.get(hid);
    if (existing) {
      existing.matters.push(matter);
    } else {
      groups.set(hid, {
        id: hid,
        name: matter.household?.name ?? 'Household',
        matters: [matter],
      });
    }
  }
  return [...groups.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function InvitationsSection(): React.JSX.Element {
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<string>('client_primary');
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [revokingId, setRevokingId] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    firmApi
      .listInvitations()
      .then((result) =>
        setInvitations(
          [...result].sort(
            (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
          ),
        ),
      )
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = email.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) {
      setFormError('Enter a valid email address.');
      return;
    }
    setCreating(true);
    setFormError(null);
    firmApi
      .createInvitation({ email: trimmed, role })
      .then(({ invitation }) => {
        setInvitations((prev) => [invitation, ...prev]);
        setEmail('');
      })
      .catch((err: unknown) =>
        setFormError(err instanceof Error ? err.message : 'Could not create the invitation.'),
      )
      .finally(() => setCreating(false));
  };

  const handleRevoke = (invitationId: string) => {
    setRevokingId(invitationId);
    firmApi
      .revokeInvitation(invitationId)
      .then((updated) => {
        setInvitations((prev) => prev.map((i) => (i.id === updated.id ? updated : i)));
      })
      .catch((err: unknown) => setError(err))
      .finally(() => setRevokingId(null));
  };

  if (loading) return <LoadingState lines={4} label="Loading invitations…" />;
  if (error != null) {
    if (error instanceof ApiError && error.isPermissionDenied) {
      return (
        <EmptyState
          title="Invitations need firm admin access"
          description="Only firm admins can manage invitations. Ask a firm admin to handle this."
        />
      );
    }
    return <ErrorState error={error} onRetry={load} />;
  }

  return (
    <div className="space-y-8">
      <Card>
        <CardHeader>
          <CardTitle>Invite someone</CardTitle>
          <CardDescription>
            Client roles join a household; firm roles join the firm team.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleCreate} className="grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <div className="space-y-1.5">
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="person@example.com"
                autoComplete="off"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invite-role">Role</Label>
              <select
                id="invite-role"
                value={role}
                onChange={(event) => setRole(event.target.value)}
                className="min-h-[44px] w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
              >
                {INVITATION_ROLES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            <Button type="submit" disabled={creating} className="min-h-[44px]">
              {creating ? 'Sending…' : 'Send invitation'}
            </Button>
          </form>
          {formError && (
            <p role="alert" className="mt-3 text-sm text-lf-error-700">
              {formError}
            </p>
          )}
        </CardContent>
      </Card>

      {invitations.length === 0 ? (
        <EmptyState
          title="No invitations yet"
          description="Sent invitations appear here with their status and expiry."
        />
      ) : (
        <Card>
          <div className="overflow-x-auto md:max-h-[75vh] md:overflow-y-auto">
            <table className="w-full text-left text-sm tabular-nums md:min-w-[640px]">
              <caption className="sr-only">Firm invitations</caption>
              <thead className="max-md:hidden md:sticky md:top-0 md:bg-white">
                <tr className="border-b border-lf-border text-xs uppercase tracking-wide text-lf-text-muted">
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Email
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Role
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Status
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Expires
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="max-md:block max-md:space-y-3 max-md:bg-lf-canvas-subtle max-md:p-4">
                {invitations.map((invitation) => (
                  <tr key={invitation.id} className="border-t border-lf-border transition-colors hover:bg-lf-canvas-subtle max-md:block max-md:rounded-lf-card max-md:border max-md:border-lf-border max-md:bg-white max-md:p-4">
                    <td data-th="Email" className={`px-4 py-3 font-medium text-lf-text ${MOBILE_TABLE_CELL}`}>{invitation.email}</td>
                    <td data-th="Role" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>
                      {formatStatus(invitation.role)}
                    </td>
                    <td data-th="Status" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>
                      {formatStatus(invitation.status)}
                    </td>
                    <td data-th="Expires" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>
                      {formatDateTime(invitation.expires_at)}
                    </td>
                    <td className={`px-4 py-3 ${MOBILE_TABLE_ACTION_CELL}`}>
                      {invitation.status === 'pending' ? (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="min-h-[44px]"
                          disabled={revokingId === invitation.id}
                          onClick={() => handleRevoke(invitation.id)}
                        >
                          {revokingId === invitation.id ? 'Revoking…' : 'Revoke'}
                        </Button>
                      ) : (
                        <span className="text-sm text-lf-text-subtle">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

/** Firm clients: households derived from matters, plus invitation management. */
export default function FirmClientsPage(): React.JSX.Element {
  const [households, setHouseholds] = useState<HouseholdGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchAllMatters()
      .then((matters) => setHouseholds(groupByHousehold(matters)))
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) return <LoadingState lines={6} label="Loading clients…" />;
  if (error != null) return <ErrorState error={error} onRetry={load} />;

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Clients</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Client households at the firm, derived from their matters, plus invitations.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Households</CardTitle>
          <CardDescription>
            Each household shows its matters — open one to manage the household in detail.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {households.length === 0 ? (
            <div className="p-6">
              <EmptyState
                title="No households yet"
                description="Households appear here once the firm starts a matter for them."
              />
            </div>
          ) : (
            <ul>
              {households.map((household) => (
                <li key={household.id} className="border-t border-lf-border first:border-t-0">
                  <div className="px-4 py-3">
                    <p className="font-medium text-lf-text">{household.name}</p>
                    <ul className="mt-2 space-y-1">
                      {household.matters.map((matter) => (
                        <li key={matter.id}>
                          <Link
                            href={`/firm/matters/${matter.id}`}
                            className="inline-flex min-h-[44px] items-center text-sm text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
                          >
                            {matter.title || 'Untitled matter'}
                            <span className="ml-2 text-lf-text-muted">
                              · {formatStatus(matter.status)}
                            </span>
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <section aria-labelledby="invitations-heading" className="space-y-4">
        <h2 id="invitations-heading" className="text-xl font-semibold text-lf-text">
          Invitations
        </h2>
        <InvitationsSection />
      </section>
    </div>
  );
}
