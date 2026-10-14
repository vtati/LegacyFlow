'use client';

import { useCallback, useEffect, useState } from 'react';
import { Pencil } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { MatterSelect, useMatters } from '@/components/family/matter-context';
import { PersonForm, type PersonFormValues } from '@/components/family/person-form';
import {
  api,
  type HouseholdMember,
  type HouseholdResponse,
  type Person,
  type PersonInput,
} from '@/lib/api-client';

function memberPerson(member: HouseholdMember, persons: Person[]): Person | null {
  if (typeof member.person === 'object') return member.person;
  return persons.find((p) => p.id === member.person) ?? null;
}

function formatAccess(status: string): string {
  return status
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

interface MemberCardProps {
  member: HouseholdMember;
  person: Person | null;
  onEdit: (person: Person) => void;
  editing: boolean;
  saving: boolean;
  formError: string | null;
  onSave: (personId: string, values: PersonFormValues) => void;
  onCancelEdit: () => void;
}

function MemberCard({
  member,
  person,
  onEdit,
  editing,
  saving,
  formError,
  onSave,
  onCancelEdit,
}: MemberCardProps) {
  return (
    <Card>
      <CardContent className="space-y-4 p-6">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="font-semibold text-lf-text">{person?.full_name ?? 'Unknown person'}</p>
            <p className="text-sm text-lf-text-muted">
              {member.relationship_role} · Access: {formatAccess(member.access_status)}
            </p>
            {person?.email && <p className="text-sm text-lf-text-muted">{person.email}</p>}
            {person?.date_of_birth && (
              <p className="text-sm text-lf-text-muted">Born {person.date_of_birth}</p>
            )}
          </div>
          {person && !editing && (
            <Button type="button" variant="outline" size="sm" onClick={() => onEdit(person)}>
              <Pencil className="size-4" aria-hidden="true" />
              Edit
            </Button>
          )}
        </div>
        {editing && person && (
          <PersonForm
            initial={person}
            kindOptions={['adult', 'minor']}
            saving={saving}
            formError={formError}
            submitLabel="Save changes"
            idPrefix={`edit-${person.id}`}
            onSubmit={(values) => onSave(person.id, values)}
            onCancel={onCancelEdit}
          />
        )}
      </CardContent>
    </Card>
  );
}

/** Household profile — members with roles, plus add and edit person forms. */
export default function FamilyProfilePage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [matterId, setMatterId] = useState<string | null>(null);
  const [household, setHousehold] = useState<HouseholdResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [addSaving, setAddSaving] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [relationshipRole, setRelationshipRole] = useState('dependent');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadHousehold = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    api
      .getHousehold(id)
      .then((response) => setHousehold(response))
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!matterId && matters.length > 0) {
      setMatterId(matters[0]?.id ?? null);
    }
  }, [matters, matterId]);

  useEffect(() => {
    if (matterId) loadHousehold(matterId);
  }, [matterId, loadHousehold]);

  const handleAdd = (values: PersonFormValues) => {
    if (!matterId) return;
    setAddSaving(true);
    setAddError(null);
    setNotice(null);
    const input: PersonInput & { relationship_role?: string } = {
      full_name: values.full_name.trim(),
      date_of_birth: values.date_of_birth || null,
      email: values.email.trim() || null,
      kind: values.kind,
      relationship_role: relationshipRole.trim() || undefined,
    };
    api
      .createPerson(matterId, input)
      .then(() => {
        setShowAdd(false);
        setRelationshipRole('');
        setNotice('Person added. Your attorney will see the updated household.');
        loadHousehold(matterId);
      })
      .catch((err: unknown) =>
        setAddError(err instanceof Error ? err.message : 'Could not add the person.'),
      )
      .finally(() => setAddSaving(false));
  };

  const handleEditSave = (personId: string, values: PersonFormValues) => {
    if (!matterId) return;
    setEditSaving(true);
    setEditError(null);
    setNotice(null);
    api
      .updatePerson(matterId, personId, {
        full_name: values.full_name.trim(),
        date_of_birth: values.date_of_birth || null,
        email: values.email.trim() || null,
        kind: values.kind,
      })
      .then(() => {
        setEditingId(null);
        setNotice('Changes saved.');
        loadHousehold(matterId);
      })
      .catch((err: unknown) =>
        setEditError(err instanceof Error ? err.message : 'Could not save the changes.'),
      )
      .finally(() => setEditSaving(false));
  };

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Household profile</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          The people in your household. Keep this accurate — your attorney builds the plan around
          it.
        </p>
      </div>

      {mattersLoading && <LoadingState lines={3} label="Loading your matters…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={reloadMatters} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your household profile will appear here once your firm starts a plan for you."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && (
        <MatterSelect matters={matters} value={matterId} onChange={setMatterId} />
      )}

      {matterId && loading && <LoadingState lines={3} label="Loading household…" />}
      {error != null && matterId != null && !loading ? (
        <ErrorState error={error} onRetry={() => loadHousehold(matterId)} />
      ) : null}

      {notice && (
        <p
          role="status"
          className="rounded-lf-control border border-lf-success-600 bg-lf-success-50 px-4 py-3 text-sm text-lf-success-700"
        >
          {notice}
        </p>
      )}

      {household && !loading && (
        <>
          <div className="space-y-4">
            {household.members.map((member) => (
              <MemberCard
                key={member.id}
                member={member}
                person={memberPerson(member, household.persons)}
                onEdit={(person) => {
                  setEditingId(person.id);
                  setEditError(null);
                }}
                editing={editingId === memberPerson(member, household.persons)?.id}
                saving={editSaving}
                formError={editError}
                onSave={handleEditSave}
                onCancelEdit={() => setEditingId(null)}
              />
            ))}
            {household.members.length === 0 && (
              <EmptyState
                title="No members yet"
                description="No one has been added to this household. Add the first person below."
              />
            )}
          </div>

          {!showAdd ? (
            <Button type="button" variant="outline" onClick={() => setShowAdd(true)}>
              Add a person
            </Button>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>Add a person</CardTitle>
                <CardDescription>
                  Only add people your firm knows about — the details should match your records.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="max-w-sm space-y-1.5">
                  <Label htmlFor="add-relationship-role">Relationship to household</Label>
                  <select
                    id="add-relationship-role"
                    value={relationshipRole}
                    onChange={(event) => setRelationshipRole(event.target.value)}
                    className="flex min-h-11 w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-4 py-2 text-sm text-lf-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-accent-500"
                  >
                    <option value="dependent">Dependent (child or other dependent)</option>
                    <option value="collaborator">Collaborator (spouse or partner)</option>
                    <option value="primary_client">Primary client</option>
                    <option value="trusted_contact">Trusted contact</option>
                  </select>
                </div>
                <PersonForm
                  kindOptions={['adult', 'minor']}
                  saving={addSaving}
                  formError={addError}
                  submitLabel="Add person"
                  idPrefix="add-person"
                  onSubmit={handleAdd}
                  onCancel={() => setShowAdd(false)}
                />
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
