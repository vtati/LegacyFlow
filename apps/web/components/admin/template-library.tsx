'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Modal } from '@/components/ui/modal';
import { EmptyState, LoadingState } from '@/components/family/page-state';
import { AdminErrorState } from '@/components/admin/admin-page-state';
import { firmApi, type DocumentTemplate, type DocumentTemplateCreateInput } from '@/lib/api-client';
import { formatDateTime, formatStatus } from '@/lib/format';

const TEMPLATE_TYPE_OPTIONS = [
  'last_will',
  'revocable_trust',
  'healthcare_directive',
  'financial_power_of_attorney',
  'guardianship_designation',
  'other',
];

interface TemplateFormState {
  name: string;
  template_type: string;
  pack_id: string;
  version: string;
  content: string;
}

const EMPTY_FORM: TemplateFormState = {
  name: '',
  template_type: '',
  pack_id: '',
  version: '',
  content: '',
};

function TemplateDetailModal({
  template,
  onClose,
}: {
  template: DocumentTemplate;
  onClose: () => void;
}) {
  return (
    <Modal
      open
      onClose={onClose}
      title={template.name}
      description={`${formatStatus(template.template_type)} · v${template.version}`}
    >
      <div className="space-y-4">
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-muted">
              Pack
            </dt>
            <dd className="mt-0.5 font-mono text-xs text-lf-text">
              {template.pack_id ?? '—'}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-muted">
              Last updated
            </dt>
            <dd className="mt-0.5 text-lf-text">{formatDateTime(template.updated_at)}</dd>
          </div>
        </dl>
        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wide text-lf-text-muted">
            Content
          </p>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lf-control border border-lf-border bg-lf-canvas-subtle p-4 font-mono text-xs leading-relaxed text-lf-text">
            {template.content}
          </pre>
        </div>
        <div className="flex justify-end">
          <Button type="button" variant="outline" className="min-h-[44px]" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function CreateTemplateModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (template: DocumentTemplate) => void;
}) {
  const [form, setForm] = useState<TemplateFormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const name = form.name.trim();
    const templateType = form.template_type.trim();
    const content = form.content.trim();
    if (name.length === 0 || templateType.length === 0 || content.length === 0) {
      setFormError('Name, template type, and content are required.');
      return;
    }
    const input: DocumentTemplateCreateInput = {
      name,
      template_type: templateType,
      content,
    };
    if (form.pack_id.trim().length > 0) input.pack_id = form.pack_id.trim();
    if (form.version.trim().length > 0) input.version = form.version.trim();
    setSaving(true);
    setFormError(null);
    firmApi
      .createTemplate(input)
      .then((created) => onCreated(created))
      .catch((err: unknown) =>
        setFormError(err instanceof Error ? err.message : 'Could not create the template.'),
      )
      .finally(() => setSaving(false));
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="New template"
      description="Create a document template for your firm. It will be available to firm users immediately."
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="template-name">Name</Label>
          <Input
            id="template-name"
            value={form.name}
            onChange={(event) => setForm((prev) => ({ ...prev, name: event.target.value }))}
            placeholder="e.g. Last Will and Testament — Standard"
            maxLength={200}
            autoComplete="off"
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="template-type">Template type</Label>
            <Input
              id="template-type"
              list="template-type-options"
              value={form.template_type}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, template_type: event.target.value }))
              }
              placeholder="last_will"
              autoComplete="off"
            />
            <datalist id="template-type-options">
              {TEMPLATE_TYPE_OPTIONS.map((option) => (
                <option key={option} value={option} />
              ))}
            </datalist>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="template-version">Version (optional)</Label>
            <Input
              id="template-version"
              value={form.version}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, version: event.target.value }))
              }
              placeholder="e.g. 1.0"
              autoComplete="off"
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="template-pack">Pack ID (optional)</Label>
          <Input
            id="template-pack"
            value={form.pack_id}
            onChange={(event) =>
              setForm((prev) => ({ ...prev, pack_id: event.target.value }))
            }
            placeholder="e.g. us-va"
            autoComplete="off"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="template-content">Content</Label>
          <textarea
            id="template-content"
            value={form.content}
            onChange={(event) =>
              setForm((prev) => ({ ...prev, content: event.target.value }))
            }
            rows={8}
            placeholder="The template body — placeholders and clauses go here."
            className="flex w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 font-mono text-sm text-lf-text shadow-lf-subtle placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          />
        </div>
        {formError && (
          <p role="alert" className="text-sm text-lf-error-700">
            {formError}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            className="min-h-[44px]"
            onClick={onClose}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button type="submit" className="min-h-[44px]" disabled={saving}>
            {saving ? 'Creating…' : 'Create template'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/**
 * Firm template library: table, detail view, and create form over the same
 * firm-scoped document-templates endpoints the firm workspace uses. Kept
 * self-contained so both /firm/templates and /admin/templates can render it.
 */
export function TemplateLibrary(): React.JSX.Element {
  const [templates, setTemplates] = useState<DocumentTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [detail, setDetail] = useState<DocumentTemplate | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    firmApi
      .listTemplates()
      .then((result) =>
        setTemplates(
          [...result].sort((a, b) =>
            new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
          ),
        ),
      )
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreated = (template: DocumentTemplate) => {
    setCreating(false);
    setTemplates((prev) => [template, ...prev]);
    setDetail(template);
  };

  if (loading) return <LoadingState lines={5} label="Loading templates…" />;
  if (error != null) return <AdminErrorState error={error} onRetry={load} />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-lf-text-muted">
          {templates.length === 0
            ? 'No templates yet.'
            : `${templates.length} ${templates.length === 1 ? 'template' : 'templates'} in your firm's library.`}
        </p>
        <Button type="button" className="min-h-[44px]" onClick={() => setCreating(true)}>
          New template
        </Button>
      </div>

      {templates.length === 0 ? (
        <EmptyState
          title="No templates yet"
          description="Create the firm's first document template. Templates are shared across your firm's matters."
          action={
            <Button type="button" className="min-h-[44px]" onClick={() => setCreating(true)}>
              New template
            </Button>
          }
        />
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <caption className="sr-only">Firm document templates</caption>
              <thead>
                <tr className="border-b border-lf-border text-xs uppercase tracking-wide text-lf-text-muted">
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Name
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Type
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Version
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Pack
                  </th>
                  <th scope="col" className="px-4 py-3 font-semibold">
                    Updated
                  </th>
                </tr>
              </thead>
              <tbody>
                {templates.map((template) => (
                  <tr key={template.id} className="border-t border-lf-border">
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        onClick={() => setDetail(template)}
                        className="rounded-lf-control text-left font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
                      >
                        {template.name}
                      </button>
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">
                      {formatStatus(template.template_type)}
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">{template.version}</td>
                    <td className="px-4 py-3 font-mono text-xs text-lf-text-muted">
                      {template.pack_id ?? '—'}
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">
                      {formatDateTime(template.updated_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {detail && <TemplateDetailModal template={detail} onClose={() => setDetail(null)} />}

      {creating && (
        <CreateTemplateModal onClose={() => setCreating(false)} onCreated={handleCreated} />
      )}
    </div>
  );
}

/** /admin/templates: firm template library rendered through the shared component. */
export function AdminTemplatesSection(): React.JSX.Element {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Firm template library</CardTitle>
        <CardDescription>
          The same firm-scoped templates your attorneys assemble documents from. Browse, inspect,
          and create versions here.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <TemplateLibrary />
      </CardContent>
    </Card>
  );
}
