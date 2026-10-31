'use client';

import { useState } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import type { AnswerValue, Person, PersonInput, Question } from '@/lib/api-client';

export type AddPersonInput = PersonInput & { relationship_role?: string };

interface QuestionFieldProps {
  question: Question;
  value: AnswerValue;
  onChange: (value: AnswerValue) => void;
  persons: Person[];
  /** Read-only presentation (the matter has left the editable
   * intake statuses — the server would reject any save). */
  disabled?: boolean;
  /** Creates a household person in-flow (person questions only).
   * Provided by the wizard; absent on read-only surfaces. */
  onAddPerson?: (input: AddPersonInput) => Promise<Person>;
}

const selectClassName =
  'h-10 w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 disabled:opacity-60';

function asString(value: AnswerValue): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return '';
}

function Help({ id, text }: { id: string; text: string }) {
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-sm text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control w-fit">
        Why we ask
      </summary>
      <p id={id} className="mt-1 max-w-prose text-sm text-lf-text-muted">
        {text}
      </p>
    </details>
  );
}

/** Renders one intake question by type: label, help, and an accessible control. */
export function QuestionField({
  question,
  value,
  onChange,
  persons,
  disabled = false,
  onAddPerson,
}: QuestionFieldProps) {
  const fieldId = `question-${question.key}`;
  const helpId = `question-${question.key}-help`;
  const helpText = question.help?.trim() ? question.help : null;
  const describedBy = helpText ? helpId : undefined;

  const labelSuffix = question.required ? (
    <span className="ml-1 text-lf-error-700" aria-hidden="true">
      *
    </span>
  ) : null;

  // Add-a-person inline form state (person questions only).
  const [addingPerson, setAddingPerson] = useState(false);
  const [newName, setNewName] = useState('');
  const [newKind, setNewKind] = useState<'adult' | 'minor'>('adult');
  const [newRole, setNewRole] = useState<'trusted_contact' | 'dependent'>('trusted_contact');
  const [addSaving, setAddSaving] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const submitNewPerson = () => {
    if (!onAddPerson || newName.trim() === '') return;
    setAddSaving(true);
    setAddError(null);
    onAddPerson({
      full_name: newName.trim(),
      date_of_birth: null,
      email: null,
      kind: newKind,
      relationship_role: newRole,
    })
      .then((created) => {
        onChange(created.id);
        setAddingPerson(false);
        setNewName('');
        setNewKind('adult');
        setNewRole('trusted_contact');
      })
      .catch(() =>
        setAddError('We could not add that person just now — please try again.'),
      )
      .finally(() => setAddSaving(false));
  };

  if (
    question.type === 'single_select' ||
    question.type === 'multi_select' ||
    question.type === 'boolean'
  ) {
    const options =
      question.type === 'boolean'
        ? [
            { value: 'true', label: 'Yes' },
            { value: 'false', label: 'No' },
          ]
        : (question.options ?? []);
    const selectedValues: string[] =
      question.type === 'multi_select' && Array.isArray(value)
        ? value
        : question.type === 'multi_select'
          ? []
          : asString(value)
            ? [asString(value)]
            : [];

    return (
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-lf-text">
          {question.label}
          {labelSuffix}
        </legend>
        {options.map((option) => {
          const optionId = `${fieldId}-${option.value}`;
          const checked = selectedValues.includes(option.value);
          return (
            <div key={option.value} className="flex items-center gap-2.5">
              <input
                id={optionId}
                type={question.type === 'multi_select' ? 'checkbox' : 'radio'}
                name={fieldId}
                value={option.value}
                checked={checked}
                disabled={disabled}
                onChange={(event) => {
                  if (question.type === 'multi_select') {
                    const next = event.target.checked
                      ? [...selectedValues, option.value]
                      : selectedValues.filter((v) => v !== option.value);
                    onChange(next);
                  } else if (question.type === 'boolean') {
                    onChange(option.value === 'true');
                  } else {
                    onChange(option.value);
                  }
                }}
                aria-describedby={describedBy}
                className="size-4 shrink-0 accent-lf-primary-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
              />
              <label htmlFor={optionId} className="text-sm text-lf-text">
                {option.label}
              </label>
            </div>
          );
        })}
        {helpText && <Help id={helpId} text={helpText} />}
      </fieldset>
    );
  }

  if (question.type === 'person') {
    return (
      <div className="space-y-1.5">
        <Label htmlFor={fieldId}>
          {question.label}
          {labelSuffix}
        </Label>
        <select
          id={fieldId}
          value={asString(value)}
          onChange={(event) => onChange(event.target.value === '' ? null : event.target.value)}
          aria-describedby={describedBy}
          disabled={disabled}
          className={selectClassName}
        >
          <option value="">Choose a person…</option>
          {persons.map((person) => (
            <option key={person.id} value={person.id}>
              {person.full_name}
            </option>
          ))}
        </select>
        {persons.length === 0 && (
          <p className="text-sm text-lf-text-muted">
            No one is in your household list yet — add the person below and they will be
            chosen for this question.
          </p>
        )}
        {onAddPerson && !disabled && !addingPerson && (
          <button
            type="button"
            onClick={() => setAddingPerson(true)}
            className="text-sm font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
          >
            ＋ Add a person
          </button>
        )}
        {onAddPerson && !disabled && addingPerson && (
          <div className="space-y-3 rounded-lf-control border border-lf-border bg-lf-canvas-subtle p-3">
            <div className="space-y-1.5">
              <Label htmlFor={`${fieldId}-new-name`}>Full name</Label>
              <Input
                id={`${fieldId}-new-name`}
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                autoComplete="off"
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor={`${fieldId}-new-kind`}>Adult or minor</Label>
                <select
                  id={`${fieldId}-new-kind`}
                  value={newKind}
                  onChange={(event) => setNewKind(event.target.value as 'adult' | 'minor')}
                  className={selectClassName}
                >
                  <option value="adult">Adult</option>
                  <option value="minor">Minor (under 18)</option>
                </select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`${fieldId}-new-role`}>Their connection to you</Label>
                <select
                  id={`${fieldId}-new-role`}
                  value={newRole}
                  onChange={(event) =>
                    setNewRole(event.target.value as 'trusted_contact' | 'dependent')
                  }
                  className={selectClassName}
                >
                  <option value="trusted_contact">
                    Trusted contact — a relative or friend I am naming in my plan
                  </option>
                  <option value="dependent">
                    Dependent — a child or family member I support
                  </option>
                </select>
              </div>
            </div>
            {addError && (
              <p role="alert" className="text-sm text-lf-error-700">
                {addError}
              </p>
            )}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={submitNewPerson}
                disabled={addSaving || newName.trim() === ''}
                className="rounded-lf-control bg-lf-primary-700 px-3 py-1.5 text-sm font-medium text-lf-primary-foreground disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
              >
                {addSaving ? 'Adding…' : 'Add and choose this person'}
              </button>
              <button
                type="button"
                onClick={() => setAddingPerson(false)}
                disabled={addSaving}
                className="rounded-lf-control px-3 py-1.5 text-sm text-lf-text-muted underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
        {helpText && <Help id={helpId} text={helpText} />}
      </div>
    );
  }

  if (question.type === 'notes') {
    return (
      <div className="space-y-1.5">
        <Label htmlFor={fieldId}>
          {question.label}
          {labelSuffix}
        </Label>
        <textarea
          id={fieldId}
          value={asString(value)}
          onChange={(event) => onChange(event.target.value)}
          rows={4}
          disabled={disabled}
          aria-describedby={describedBy}
          className="flex w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
        />
        {helpText && <Help id={helpId} text={helpText} />}
      </div>
    );
  }

  // text, date, and any future single-value type fall back to a text input.
  return (
    <div className="space-y-1.5">
      <Label htmlFor={fieldId}>
        {question.label}
        {labelSuffix}
      </Label>
      <Input
        id={fieldId}
        type={question.type === 'date' ? 'date' : 'text'}
        value={asString(value)}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        aria-describedby={describedBy}
      />
      {helpText && <Help id={helpId} text={helpText} />}
    </div>
  );
}
