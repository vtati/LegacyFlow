'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import {
  ApiError,
  adminApi,
  firmApi,
  type EffectiveFeatureFlag,
  type FirmMember,
  type FirmProfile,
  type Invitation,
} from '@/lib/api-client';
import { formatDateTime, formatStatus } from '@/lib/format';
import { cn } from '@/lib/utils';

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

function FirmProfileCard({ firm }: { firm: FirmProfile }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Firm profile</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-2 text-sm sm:grid-cols-2">
        <div>
          <p className="text-lf-text-subtle">Display name</p>
          <p className="font-medium text-lf-text">{firm.display_name}</p>
        </div>
        <div>
          <p className="text-lf-text-subtle">Legal name</p>
          <p className="font-medium text-lf-text">{firm.legal_name}</p>
        </div>
        <div>
          <p className="text-lf-text-subtle">Status</p>
          <p className="font-medium text-lf-text">{formatStatus(firm.status)}</p>
        </div>
        <div>
          <p className="text-lf-text-subtle">Created</p>
          <p className="font-medium text-lf-text">{formatDateTime(firm.created_at)}</p>
        </div>
      </CardContent>
    </Card>
  );
}

function MemberRoster({ members }: { members: FirmMember[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Team members</CardTitle>
        <CardDescription>
          Everyone with a role at the firm, and their access status.
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        {members.length === 0 ? (
          <div className="p-6">
            <EmptyState title="No members" description="The firm roster is empty." />
          </div>
        ) : (
          <div className="overflow-x-auto md:max-h-[75vh] md:overflow-y-auto">
            <table className="w-full text-left text-sm tabular-nums md:min-w-[560px]">
              <caption className="sr-only">Firm members</caption>
              <thead className="max-md:hidden md:sticky md:top-0 md:bg-lf-canvas-elevated">
                <tr className="border-b border-lf-border text-xs uppercase tracking-wide text-lf-text-muted">
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Name
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Email
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Role
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody className="max-md:block max-md:space-y-3 max-md:bg-lf-canvas-subtle max-md:p-4">
                {members.map((member) => (
                  <tr key={member.id} className="border-t border-lf-border transition-colors hover:bg-lf-canvas-subtle max-md:block max-md:rounded-lf-card max-md:border max-md:border-lf-border max-md:bg-lf-canvas-elevated max-md:p-4">
                    <td data-th="Name" className={`px-4 py-3 font-medium text-lf-text ${MOBILE_TABLE_CELL}`}>
                      {member.display_name ?? '—'}
                    </td>
                    <td data-th="Email" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>{member.email ?? '—'}</td>
                    <td data-th="Role" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>
                      {formatStatus(member.role)}
                    </td>
                    <td data-th="Status" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>
                      {formatStatus(member.status)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function InvitationsManager() {
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<string>('firm_staff');
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [revokeError, setRevokeError] = useState<string | null>(null);

  useEffect(() => {
    firmApi
      .listInvitations()
      .then((result) =>
        setInvitations(
          [...result].sort(
            (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
          ),
        ),
      )
      .finally(() => setLoading(false));
  }, []);

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
    setRevokeError(null);
    firmApi
      .revokeInvitation(invitationId)
      .then((updated) =>
        setInvitations((prev) => prev.map((i) => (i.id === updated.id ? updated : i))),
      )
      // trackSave stays silent on 4xx (Stage F F3), so this inline error is
      // the only surface a failed revoke gets — it must catch every failure.
      .catch((err: unknown) =>
        setRevokeError(err instanceof Error ? err.message : 'Could not revoke the invitation.'),
      )
      .finally(() => setRevokingId(null));
  };

  if (loading) return <LoadingState lines={3} label="Loading invitations…" />;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Invite someone</CardTitle>
          <CardDescription>Firm roles join the team; client roles join a household.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleCreate} className="grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <div className="space-y-1.5">
              <Label htmlFor="team-invite-email">Email</Label>
              <Input
                id="team-invite-email"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="person@example.com"
                autoComplete="off"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="team-invite-role">Role</Label>
              <select
                id="team-invite-role"
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

      {revokeError && (
        <p role="alert" className="text-sm text-lf-error-700">
          {revokeError}
        </p>
      )}

      {invitations.length > 0 && (
        <Card>
          <div className="overflow-x-auto md:max-h-[75vh] md:overflow-y-auto">
            <table className="w-full text-left text-sm tabular-nums md:min-w-[560px]">
              <caption className="sr-only">Firm invitations</caption>
              <thead className="max-md:hidden md:sticky md:top-0 md:bg-lf-canvas-elevated">
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
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="max-md:block max-md:space-y-3 max-md:bg-lf-canvas-subtle max-md:p-4">
                {invitations.map((invitation) => (
                  <tr key={invitation.id} className="border-t border-lf-border transition-colors hover:bg-lf-canvas-subtle max-md:block max-md:rounded-lf-card max-md:border max-md:border-lf-border max-md:bg-lf-canvas-elevated max-md:p-4">
                    <td data-th="Email" className={`px-4 py-3 font-medium text-lf-text ${MOBILE_TABLE_CELL}`}>{invitation.email}</td>
                    <td data-th="Role" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>
                      {formatStatus(invitation.role)}
                    </td>
                    <td data-th="Status" className={`px-4 py-3 text-sm text-lf-text-muted ${MOBILE_TABLE_CELL}`}>
                      {formatStatus(invitation.status)}
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

function FeatureFlags() {
  const [flags, setFlags] = useState<EffectiveFeatureFlag[]>([]);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    adminApi
      .listFeatureFlags()
      .then((result) => setFlags([...result].sort((a, b) => a.key.localeCompare(b.key))))
      .finally(() => setLoading(false));
  }, []);

  const handleToggle = (flag: EffectiveFeatureFlag) => {
    setToggling(flag.key);
    setNotice(null);
    adminApi
      .setFeatureFlag(flag.key, !flag.is_enabled)
      .then((updated) => {
        setFlags((prev) =>
          prev.map((f) => (f.key === flag.key ? { ...f, is_enabled: updated.is_enabled } : f)),
        );
      })
      .catch((err: unknown) =>
        setNotice(err instanceof Error ? err.message : 'Could not update the flag.'),
      )
      .finally(() => setToggling(null));
  };

  if (loading) return <LoadingState lines={3} label="Loading feature flags…" />;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Feature flags</CardTitle>
        <CardDescription>Rollout controls for new capabilities at the firm.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-1">
        {notice && (
          <p role="alert" className="text-sm text-lf-error-700">
            {notice}
          </p>
        )}
        {flags.length === 0 ? (
          <EmptyState title="No feature flags" description="No flags are configured for the firm." />
        ) : (
          <ul>
            {flags.map((flag) => (
              <li
                key={flag.key}
                className="flex min-h-[56px] items-center justify-between gap-4 border-t border-lf-border py-2 first:border-t-0"
              >
                <span>
                  <span className="block font-mono text-sm text-lf-text">{flag.key}</span>
                  <span className="block text-xs text-lf-text-subtle">Source: {flag.source}</span>
                </span>
                <button
                  type="button"
                  role="switch"
                  aria-checked={flag.is_enabled}
                  aria-label={`Toggle ${flag.key}`}
                  disabled={toggling === flag.key}
                  onClick={() => handleToggle(flag)}
                  className={cn(
                    'flex h-8 min-h-[44px] w-16 shrink-0 items-center rounded-full px-1 transition-colors',
                    flag.is_enabled ? 'justify-end bg-lf-success-600' : 'justify-start bg-lf-canvas-subtle',
                  )}
                >
                  <span className="flex size-6 items-center justify-center rounded-full bg-white text-[10px] font-semibold text-lf-text shadow dark:text-lf-primary-950">
                    {flag.is_enabled ? 'On' : 'Off'}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

/** Team & settings: firm profile, roster, invitations, feature flags (firm_admin). */
export default function FirmTeamSettingsPage(): React.JSX.Element {
  const [firm, setFirm] = useState<FirmProfile | null>(null);
  const [members, setMembers] = useState<FirmMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoading(true);
    setForbidden(false);
    setError(null);
    Promise.all([adminApi.getCurrentFirm(), adminApi.listFirmMembers()])
      .then(([profile, roster]) => {
        setFirm(profile);
        setMembers(
          [...roster].sort((a, b) =>
            (a.display_name ?? a.email ?? '').localeCompare(b.display_name ?? b.email ?? ''),
          ),
        );
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.isPermissionDenied) setForbidden(true);
        else setError(err);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) return <LoadingState lines={6} label="Loading team settings…" />;
  if (forbidden) {
    return (
      <div className="mx-auto max-w-5xl">
        <EmptyState
          title="Requires firm admin"
          description="Team & settings is only available to firm admins. Ask a firm admin to manage the roster, invitations, and feature flags."
        />
      </div>
    );
  }
  if (error != null) return <ErrorState error={error} onRetry={load} />;
  if (!firm) return <LoadingState lines={4} label="Loading team settings…" />;

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Team & settings</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Firm profile, team roster, invitations, and feature flags.
        </p>
      </div>

      <FirmProfileCard firm={firm} />
      <MemberRoster members={members} />

      <section aria-labelledby="team-invitations-heading" className="space-y-4">
        <h2 id="team-invitations-heading" className="text-xl font-semibold text-lf-text">
          Invitations
        </h2>
        <InvitationsManager />
      </section>

      <section aria-labelledby="feature-flags-heading" className="space-y-4">
        <h2 id="feature-flags-heading" className="text-xl font-semibold text-lf-text">
          Feature flags
        </h2>
        <FeatureFlags />
      </section>
    </div>
  );
}
