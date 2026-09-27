'use client';

import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import type { AnswerValue, Person, Question } from '@/lib/api-client';

interface QuestionFieldProps {
  question: Question;
  value: AnswerValue;
  onChange: (value: AnswerValue) => void;
  persons: Person[];
}

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
export function QuestionField({ question, value, onChange, persons }: QuestionFieldProps) {
  const fieldId = `question-${question.key}`;
  const helpId = `question-${question.key}-help`;
  const helpText = question.help?.trim() ? question.help : null;
  const describedBy = helpText ? helpId : undefined;

  const labelSuffix = question.required ? (
    <span className="ml-1 text-lf-error-700" aria-hidden="true">
      *
    </span>
  ) : null;

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
          className="h-10 w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
        >
          <option value="">Choose a person…</option>
          {persons.map((person) => (
            <option key={person.id} value={person.id}>
              {person.full_name}
            </option>
          ))}
        </select>
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
        aria-describedby={describedBy}
      />
      {helpText && <Help id={helpId} text={helpText} />}
    </div>
  );
}
