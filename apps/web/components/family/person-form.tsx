'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent } from '@/components/ui/card';
import type { Person } from '@/lib/api-client';

export interface PersonFormValues {
  full_name: string;
  date_of_birth: string;
  email: string;
  kind: string;
}

interface PersonFormProps {
  initial?: Person;
  kindOptions: string[];
  fixedKind?: string;
  saving: boolean;
  formError: string | null;
  onSubmit: (values: PersonFormValues) => void;
  onCancel?: () => void;
  submitLabel: string;
  idPrefix: string;
}

function toValues(person?: Person): PersonFormValues {
  return {
    full_name: person?.full_name ?? '',
    date_of_birth: person?.date_of_birth ?? '',
    email: person?.email ?? '',
    kind: person?.kind ?? 'adult',
  };
}

/** Shared add/edit form for a person. Dates are ISO (YYYY-MM-DD). */
export function PersonForm({
  initial,
  kindOptions,
  fixedKind,
  saving,
  formError,
  onSubmit,
  onCancel,
  submitLabel,
  idPrefix,
}: PersonFormProps) {
  const [values, setValues] = useState<PersonFormValues>(() => toValues(initial));
  const [validationError, setValidationError] = useState<string | null>(null);

  const set = <K extends keyof PersonFormValues>(key: K, value: PersonFormValues[K]) =>
    setValues((prev) => ({ ...prev, [key]: value }));

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!values.full_name.trim()) {
      setValidationError('Please enter a full name.');
      return;
    }
    setValidationError(null);
    onSubmit({ ...values, kind: fixedKind ?? values.kind });
  };

  const shownError = validationError ?? formError;

  return (
    <form onSubmit={handleSubmit} className="space-y-4" noValidate={false}>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-full-name`}>Full name</Label>
          <Input
            id={`${idPrefix}-full-name`}
            value={values.full_name}
            onChange={(event) => set('full_name', event.target.value)}
            required
            autoComplete="name"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-dob`}>Date of birth</Label>
          <Input
            id={`${idPrefix}-dob`}
            type="date"
            value={values.date_of_birth}
            onChange={(event) => set('date_of_birth', event.target.value)}
            autoComplete="bday"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-email`}>Email</Label>
          <Input
            id={`${idPrefix}-email`}
            type="email"
            value={values.email}
            onChange={(event) => set('email', event.target.value)}
            autoComplete="email"
            placeholder="Optional"
          />
        </div>
        {!fixedKind && (
          <div className="space-y-1.5">
            <Label htmlFor={`${idPrefix}-kind`}>Type</Label>
            <select
              id={`${idPrefix}-kind`}
              value={values.kind}
              onChange={(event) => set('kind', event.target.value)}
              className="h-10 w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
            >
              {kindOptions.map((kind) => (
                <option key={kind} value={kind}>
                  {kind.charAt(0).toUpperCase() + kind.slice(1)}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
      {shownError && (
        <Card className="border-lf-error-600">
          <CardContent className="pt-4">
            <p role="alert" className="text-sm text-lf-error-700">
              {shownError}
            </p>
          </CardContent>
        </Card>
      )}
      <div className="flex gap-3">
        <Button type="submit" disabled={saving}>
          {saving ? 'Saving…' : submitLabel}
        </Button>
        {onCancel && (
          <Button type="button" variant="outline" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}
