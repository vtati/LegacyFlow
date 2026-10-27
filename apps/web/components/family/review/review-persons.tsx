'use client';

import { useState } from 'react';
import { Check, Pencil, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import type { Nomination, Person } from '@/lib/api-client';

const NOMINATION_LABELS: Record<string, string> = {
  guardian: 'Guardian',
  executor: 'Executor',
  trustee: 'Trustee',
  financial_agent: 'Financial agent',
  healthcare_agent: 'Healthcare agent',
};

interface ReviewPersonsProps {
  persons: Person[];
  nominations: Nomination[];
  onSavePersonName: (person: Person, fullName: string) => Promise<void>;
}

function PersonRow({
  person,
  onSavePersonName,
}: {
  person: Person;
  onSavePersonName: (person: Person, fullName: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(person.full_name);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const save = async () => {
    const name = draft.trim();
    if (!name) {
      setSaveError('A name is required.');
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      await onSavePersonName(person, name);
      setEditing(false);
    } catch {
      setSaveError('Could not save — please try again.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex items-start justify-between gap-3 py-2.5">
      <div className="min-w-0">
        <p className="text-sm font-medium text-lf-text">{person.full_name}</p>
        <p className="text-xs text-lf-text-subtle">
          {person.kind === 'minor' ? 'Minor child' : 'Adult'}
          {person.date_of_birth ? ` · Born ${person.date_of_birth}` : ''}
        </p>
        {editing && (
          <div className="mt-3 space-y-3">
            <label className="block text-sm text-lf-text-muted" htmlFor={`person-name-${person.id}`}>
              Full legal name
              <Input
                id={`person-name-${person.id}`}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                className="mt-1"
              />
            </label>
            {saveError && (
              <p role="alert" className="text-sm text-lf-error-700">
                {saveError}
              </p>
            )}
            <div className="flex gap-2">
              <Button type="button" size="sm" onClick={save} disabled={saving}>
                <Check className="size-4" aria-hidden="true" />
                {saving ? 'Saving…' : 'Save'}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setEditing(false)}
                disabled={saving}
              >
                <X className="size-4" aria-hidden="true" />
                Cancel
              </Button>
            </div>
          </div>
        )}
      </div>
      {!editing && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => {
            setDraft(person.full_name);
            setEditing(true);
          }}
          aria-label={`Edit name: ${person.full_name}`}
          className="shrink-0"
        >
          <Pencil className="size-4" aria-hidden="true" />
          Edit
        </Button>
      )}
    </div>
  );
}

/**
 * Persons & roles: household persons with name-spelling edits (the names
 * print on every document, so this is where typos get fixed), and the
 * nominations those answers produce — shown resolved and read-only,
 * because nominations materialize from the answers/persons above them.
 */
export function ReviewPersons({
  persons,
  nominations,
  onSavePersonName,
}: ReviewPersonsProps): React.JSX.Element {
  const personName = (personId: string): string =>
    persons.find((person) => person.id === personId)?.full_name ?? 'Unnamed person';
  const sortedNominations = [...nominations].sort(
    (a, b) =>
      a.nomination_type.localeCompare(b.nomination_type) || a.priority - b.priority,
  );
  return (
    <section
      aria-label="Persons and roles"
      className="rounded-lf-card bg-lf-panel-mint p-4 sm:p-5"
    >
      <h3 className="text-sm font-semibold text-lf-text">Persons &amp; roles</h3>
      <p className="mt-1 text-sm text-lf-text-muted">
        Check every name exactly as it should print on your documents.
      </p>
      <div className="mt-2 divide-y divide-lf-success-200">
        {persons.map((person) => (
          <PersonRow key={person.id} person={person} onSavePersonName={onSavePersonName} />
        ))}
        {persons.length === 0 && (
          <p className="py-2.5 text-sm text-lf-text-muted">No persons recorded yet.</p>
        )}
      </div>

      <h4 className="mt-5 text-xs font-semibold uppercase tracking-wide text-lf-text-subtle">
        Who fills each role
      </h4>
      {sortedNominations.length === 0 ? (
        <p className="mt-1 text-sm text-lf-text-muted">
          No roles assigned yet — name people in your answers above.
        </p>
      ) : (
        <dl className="mt-1 divide-y divide-lf-success-200">
          {sortedNominations.map((nomination) => (
            <div
              key={nomination.id}
              className="flex items-center justify-between gap-3 py-2.5"
            >
              <dt className="text-sm text-lf-text-muted">
                {NOMINATION_LABELS[nomination.nomination_type] ?? nomination.nomination_type}
                {nomination.priority > 1 ? ` (backup ${nomination.priority - 1})` : ''}
              </dt>
              <dd className="text-sm font-medium text-lf-text">
                {personName(nomination.person_id)}
              </dd>
            </div>
          ))}
        </dl>
      )}
      <p className="mt-3 text-xs text-lf-text-subtle">
        Roles are filled from your answers — to change who fills a role, edit the answer
        above or the person&apos;s name.
      </p>
    </section>
  );
}
