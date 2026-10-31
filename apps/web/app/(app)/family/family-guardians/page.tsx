'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Baby, UserRound } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { MatterSelect, useMatters } from '@/components/family/matter-context';
import {
  api,
  type HouseholdMember,
  type Nomination,
  type NominationType,
  type Person,
} from '@/lib/api-client';
import { formatDate, formatStatus } from '@/lib/format';
import { familyStatusLabel } from '@/components/family/status-pill';
import { cn } from '@/lib/utils';

const NOMINATION_TYPES: NominationType[] = [
  'guardian',
  'trustee',
  'executor',
  'financial_agent',
  'healthcare_agent',
];

const ROLE_LABELS: Record<NominationType, string> = {
  guardian: 'Guardian',
  trustee: 'Trustee',
  executor: 'Executor',
  financial_agent: 'Financial agent',
  healthcare_agent: 'Healthcare agent',
};

function memberPersonId(member: HouseholdMember): string | null {
  if (!member.person) return null;
  return typeof member.person === 'string' ? member.person : (member.person.id ?? null);
}

function memberRole(members: HouseholdMember[], personId: string): string | null {
  const member = members.find(
    (m) => m.id === personId || memberPersonId(m) === personId,
  );
  if (!member) return null;
  return member.relationship_role || null;
}

function PersonRow({
  person,
  role,
  minor,
}: {
  person: Person;
  role: string | null;
  minor: boolean;
}) {
  return (
    <li className="flex items-start justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-lf-text">{person.full_name}</p>
        <p className="mt-0.5 text-xs text-lf-text-subtle">
          {[role ? formatStatus(role) : null, person.date_of_birth ? `Born ${formatDate(person.date_of_birth)}` : null, person.email]
            .filter(Boolean)
            .join(' · ')}
        </p>
      </div>
      <span
        className={cn(
          'inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium',
          minor ? 'bg-lf-accent-100 text-lf-accent-800' : 'bg-lf-primary-50 text-lf-primary-800',
        )}
      >
        {minor ? <Baby className="size-3.5" aria-hidden="true" /> : <UserRound className="size-3.5" aria-hidden="true" />}
        {minor ? 'Minor' : 'Adult'}
      </span>
    </li>
  );
}

function NominationRow({ nomination, persons }: { nomination: Nomination; persons: Person[] }) {
  const person = persons.find((p) => p.id === nomination.person_id);
  return (
    <li className="flex items-start justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-lf-text">{person?.full_name ?? 'Unnamed person'}</p>
        <p className="mt-0.5 text-xs text-lf-text-subtle">
          Priority {nomination.priority}
          {nomination.status ? ` · ${formatStatus(nomination.status)}` : ''}
          {nomination.notes ? ` · ${nomination.notes}` : ''}
        </p>
      </div>
      <span className="shrink-0 rounded-full bg-lf-primary-50 px-2.5 py-1 text-xs font-medium text-lf-primary-800">
        {ROLE_LABELS[nomination.nomination_type as NominationType] ?? nomination.nomination_type}
      </span>
    </li>
  );
}

/** Family & Guardians — read-only combined view of household people and nominations. */
export default function FamilyGuardiansPage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [matterId, setMatterId] = useState<string | null>(null);
  const [persons, setPersons] = useState<Person[]>([]);
  const [members, setMembers] = useState<HouseholdMember[]>([]);
  const [nominations, setNominations] = useState<Nomination[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    if (!matterId && matters.length > 0) {
      setMatterId(matters[0]?.id ?? null);
    }
  }, [matters, matterId]);

  const load = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    Promise.allSettled([
      api.getHousehold(id),
      ...NOMINATION_TYPES.map((type) => api.listNominations(id, type)),
    ])
      .then((results) => {
        const householdResult = results[0];
        if (householdResult.status !== 'fulfilled') {
          throw householdResult.reason;
        }
        setPersons(householdResult.value.persons);
        setMembers(householdResult.value.members);
        const collected: Nomination[] = [];
        for (const result of results.slice(1)) {
          if (result.status === 'fulfilled' && Array.isArray(result.value)) {
            collected.push(...result.value);
          }
        }
        collected.sort((a, b) => a.priority - b.priority);
        setNominations(collected);
      })
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (matterId) load(matterId);
  }, [matterId, load]);

  const adults = persons.filter((person) => person.kind !== 'minor');
  const minors = persons.filter((person) => person.kind === 'minor');
  const selectedMatter = matters.find((matter) => matter.id === matterId);

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Family &amp; Guardians</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Everyone in your household alongside the people you have nominated to the key roles.
          Nothing here is final until your attorney approves it.
        </p>
      </div>

      {mattersLoading && <LoadingState lines={3} label="Loading your matters…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={reloadMatters} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your household and nominations will appear here once your firm starts a plan for your household."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && (
        <MatterSelect matters={matters} value={matterId} onChange={setMatterId} />
      )}

      {matterId && loading && <LoadingState lines={5} label="Loading family and guardians…" />}
      {error != null && matterId != null && !loading ? (
        <ErrorState error={error} onRetry={() => load(matterId)} />
      ) : null}

      {matterId && !loading && !error && (
        <>
          {selectedMatter && (
            <p className="text-sm text-lf-text-muted">
              {selectedMatter.household?.name ?? 'Household'} · {familyStatusLabel(selectedMatter.status)}
            </p>
          )}

          <section aria-label="Household people" className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-semibold text-lf-text">Household people</h2>
              <Link
                href="/family/dependents"
                className="inline-flex min-h-11 items-center gap-1.5 rounded-lf-control text-sm font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
              >
                Edit in Dependents <ArrowRight className="size-4" aria-hidden="true" />
              </Link>
            </div>
            {persons.length === 0 ? (
              <EmptyState
                title="No people yet"
                description="Add your household members and dependents to get started."
                action={
                  <Link
                    href="/family/dependents"
                    className="inline-flex min-h-11 items-center rounded-lf-control bg-lf-primary-700 px-4 py-2 text-sm font-medium text-white hover:bg-lf-primary-800 dark:text-lf-primary-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
                  >
                    Add people
                  </Link>
                }
              />
            ) : (
              <div className="grid gap-4 md:grid-cols-2">
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-base">Adults ({adults.length})</CardTitle>
                  </CardHeader>
                  <CardContent className="p-0">
                    {adults.length === 0 ? (
                      <p className="px-4 py-6 text-sm text-lf-text-muted">No adults recorded.</p>
                    ) : (
                      <ul className="divide-y divide-lf-border">
                        {adults.map((person) => (
                          <PersonRow
                            key={person.id}
                            person={person}
                            role={memberRole(members, person.id)}
                            minor={false}
                          />
                        ))}
                      </ul>
                    )}
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-base">Minors ({minors.length})</CardTitle>
                  </CardHeader>
                  <CardContent className="p-0">
                    {minors.length === 0 ? (
                      <p className="px-4 py-6 text-sm text-lf-text-muted">No minors recorded.</p>
                    ) : (
                      <ul className="divide-y divide-lf-border">
                        {minors.map((person) => (
                          <PersonRow
                            key={person.id}
                            person={person}
                            role={memberRole(members, person.id)}
                            minor
                          />
                        ))}
                      </ul>
                    )}
                  </CardContent>
                </Card>
              </div>
            )}
          </section>

          <section aria-label="Nominations" className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-lg font-semibold text-lf-text">Guardian nominations</h2>
              <Link
                href="/family/guardians"
                className="inline-flex min-h-11 items-center gap-1.5 rounded-lf-control text-sm font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
              >
                Edit in Guardians <ArrowRight className="size-4" aria-hidden="true" />
              </Link>
            </div>
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>
                  Who steps in for each role, in priority order. Add or remove nominations in
                  Guardians.
                </CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                {nominations.length === 0 ? (
                  <p className="px-4 py-6 text-sm text-lf-text-muted">
                    No nominations yet. Name a guardian, trustee, executor, or agent to get
                    started.
                  </p>
                ) : (
                  <ul className="divide-y divide-lf-border">
                    {nominations.map((nomination) => (
                      <NominationRow key={nomination.id} nomination={nomination} persons={persons} />
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </section>
        </>
      )}
    </div>
  );
}
