'use client';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ProgressBar } from '@/components/family/progress-bar';
import type { HouseholdResponse, MatterDetail, Person } from '@/lib/api-client';
import { formatDate, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

interface MatterOverviewProps {
  matter: MatterDetail;
  household: HouseholdResponse | null;
}

function personById(persons: Person[], id: string): Person | undefined {
  return persons.find((person) => person.id === id);
}

/** Overview tab: household, jurisdiction, status, progress, and key persons. */
export function MatterOverview({ matter, household }: MatterOverviewProps): React.JSX.Element {
  const members = household?.members ?? [];
  const persons = household?.persons ?? [];

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Matter summary</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-4 sm:grid-cols-2">
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-subtle">
                Household
              </dt>
              <dd className="mt-1 text-sm font-medium text-lf-text">{matter.household.name}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-subtle">
                Jurisdiction
              </dt>
              <dd className="mt-1 text-sm font-medium text-lf-text">
                {matter.matter.jurisdiction_code}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-subtle">
                Status
              </dt>
              <dd className="mt-1">
                <span
                  className={cn(
                    'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
                    statusBadgeClass(matter.matter.status),
                  )}
                >
                  {formatStatus(matter.matter.status)}
                </span>
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-subtle">
                Updated
              </dt>
              <dd className="mt-1 text-sm text-lf-text">{formatDate(matter.matter.updated_at)}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-subtle">
                Completeness
              </dt>
              <dd className="mt-2 flex items-center gap-3">
                <ProgressBar
                  value={matter.progress.completeness_pct}
                  label={`Matter progress: ${Math.round(matter.progress.completeness_pct)}% complete`}
                  className="flex-1"
                />
                <span className="shrink-0 text-xs font-medium text-lf-text-muted">
                  {Math.round(matter.progress.completeness_pct)}%
                </span>
              </dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Key persons</CardTitle>
        </CardHeader>
        <CardContent>
          {members.length === 0 ? (
            <p className="text-sm text-lf-text-muted">
              No household members are linked to this matter yet.
            </p>
          ) : (
            <ul className="divide-y divide-lf-border">
              {members.map((member) => {
                const person =
                  typeof member.person === 'string'
                    ? personById(persons, member.person)
                    : member.person;
                return (
                  <li key={member.id} className="flex items-start justify-between gap-3 py-3">
                    <div>
                      <p className="text-sm font-medium text-lf-text">
                        {person?.full_name ?? 'Unknown person'}
                      </p>
                      <p className="text-xs text-lf-text-subtle">
                        {person?.email ?? 'No email on file'}
                      </p>
                    </div>
                    <span className="shrink-0 rounded-full bg-lf-primary-50 px-2.5 py-1 text-xs font-medium text-lf-primary-800">
                      {formatStatus(member.relationship_role)}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
