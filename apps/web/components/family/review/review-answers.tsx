'use client';

import { useState } from 'react';
import { Check, Pencil, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { QuestionField } from '@/components/family/intake/question-field';
import type {
  AnswerMap,
  AnswerValue,
  Person,
  Question,
  QuestionSetsResponse,
} from '@/lib/api-client';

/** Format an answer the way the documents will read it. */
export function formatAnswer(
  question: Question,
  value: AnswerValue | undefined,
  persons: Person[],
): string {
  if (value == null || value === '' || (Array.isArray(value) && value.length === 0)) {
    return 'Not answered';
  }
  if (question.type === 'boolean') {
    return value === true || value === 'true' ? 'Yes' : 'No';
  }
  if (question.type === 'single_select') {
    const option = question.options?.find((opt) => opt.value === value);
    return option?.label ?? String(value);
  }
  if (question.type === 'multi_select' && Array.isArray(value)) {
    return value
      .map((entry) => question.options?.find((opt) => opt.value === entry)?.label ?? entry)
      .join(', ');
  }
  if (question.type === 'person') {
    // Interview answers carry the person's name; wizard answers carry the
    // person id — resolve either to the name the documents will print.
    const person = persons.find((candidate) => candidate.id === value);
    return person?.full_name ?? String(value);
  }
  if (Array.isArray(value)) return value.join(', ');
  return String(value);
}

interface ReviewAnswersProps {
  questionSets: QuestionSetsResponse;
  answers: AnswerMap;
  persons: Person[];
  onSaveAnswer: (key: string, value: AnswerValue) => Promise<void>;
}

function AnswerRow({
  question,
  value,
  persons,
  onSaveAnswer,
}: {
  question: Question;
  value: AnswerValue | undefined;
  persons: Person[];
  onSaveAnswer: (key: string, value: AnswerValue) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<AnswerValue>(value ?? null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const startEdit = () => {
    setDraft(value ?? null);
    setSaveError(null);
    setEditing(true);
  };

  // Person answers arrive in two shapes: the wizard stores a person id,
  // the interview stores the person's name. QuestionField's select only
  // offers person ids, so a name-string value would open on the empty
  // "Choose a person…" option with the current answer invisible — add
  // the stored name as its own option so the control shows it.
  const editPersons: Person[] =
    question.type === 'person' &&
    typeof value === 'string' &&
    value !== '' &&
    !persons.some((person) => person.id === value)
      ? [
          ...persons,
          { id: value, full_name: value, date_of_birth: null, email: null, kind: 'adult' },
        ]
      : persons;

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      await onSaveAnswer(question.key, draft);
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
        <dt className="text-sm text-lf-text-muted">
          {question.label}
          {question.required && (
            <span className="ml-1 text-lf-error-700" aria-hidden="true">
              *
            </span>
          )}
        </dt>
        <dd className="mt-0.5 text-sm font-medium text-lf-text">
          {formatAnswer(question, value, persons)}
        </dd>
        {editing && (
          <div className="mt-3 space-y-3">
            <QuestionField
              question={question}
              value={draft}
              onChange={setDraft}
              persons={editPersons}
            />
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
          onClick={startEdit}
          aria-label={`Edit answer: ${question.label}`}
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
 * Every intake answer grouped by question set + section, shown as the
 * documents will read it, with inline editing through the same field
 * components the intake wizard uses.
 */
export function ReviewAnswers({
  questionSets,
  answers,
  persons,
  onSaveAnswer,
}: ReviewAnswersProps): React.JSX.Element {
  const sets = Object.values(questionSets.question_sets);
  return (
    <div className="space-y-6">
      {sets.map((set) => (
        <section
          key={set.question_set.id}
          aria-label={set.question_set.title}
          className="rounded-lf-card bg-lf-panel-sky p-4 sm:p-5"
        >
          <h3 className="text-sm font-semibold text-lf-text">{set.question_set.title}</h3>
          {set.sections.map((section) => (
            <div key={section.id} className="mt-3">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-lf-text-subtle">
                {section.title}
              </h4>
              <dl className="mt-1 divide-y divide-lf-border/60">
                {section.questions.map((question) => (
                  <AnswerRow
                    key={question.key}
                    question={question}
                    value={answers[question.key]}
                    persons={persons}
                    onSaveAnswer={onSaveAnswer}
                  />
                ))}
              </dl>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
