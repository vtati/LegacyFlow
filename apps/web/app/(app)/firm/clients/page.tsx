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
    const existing = groups.get(matter.household.id);
    if (existing) {
      existing.matters.push(matter);
    } else {
      groups.set(matter.household.id, {
        id: matter.household.id,
        name: matter.household.name,
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
    <div className="space-y-6">
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
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <caption className="sr-only">Firm invitations</caption>
              <thead>
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
              <tbody>
                {invitations.map((invitation) => (
                  <tr key={invitation.id} className="border-t border-lf-border">
                    <td className="px-4 py-3 font-medium text-lf-text">{invitation.email}</td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">
                      {formatStatus(invitation.role)}
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">
                      {formatStatus(invitation.status)}
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">
                      {formatDateTime(invitation.expires_at)}
                    </td>
                    <td className="px-4 py-3">
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
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Clients</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
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
            <div className="p-5">
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
