'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Modal } from '@/components/ui/modal';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { ApiError, firmApi, type DocumentTemplate } from '@/lib/api-client';
import { formatDateTime, formatStatus } from '@/lib/format';

/** Server values: Literal["will", "trust", "poa", "instruction_letter"]. */
const TEMPLATE_TYPES = [
  { value: 'will', label: 'Will' },
  { value: 'trust', label: 'Trust' },
  { value: 'poa', label: 'Power of attorney' },
  { value: 'instruction_letter', label: 'Instruction letter' },
] as const;

function CreateTemplateForm({ onCreated }: { onCreated: (template: DocumentTemplate) => void }) {
  const [name, setName] = useState('');
  const [templateType, setTemplateType] = useState<string>('will');
  const [packId, setPackId] = useState('');
  const [version, setVersion] = useState('1.0');
  const [content, setContent] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (name.trim().length === 0) {
      setFormError('Give the template a name.');
      return;
    }
    if (content.trim().length === 0) {
      setFormError('Add the template content.');
      return;
    }
    setSaving(true);
    setFormError(null);
    firmApi
      .createTemplate({
        name: name.trim(),
        template_type: templateType,
        pack_id: packId.trim() === '' ? null : packId.trim(),
        version: version.trim() === '' ? undefined : version.trim(),
        content: content,
      })
      .then((template) => {
        onCreated(template);
        setName('');
        setPackId('');
        setVersion('1.0');
        setContent('');
      })
      .catch((err: unknown) =>
        setFormError(err instanceof Error ? err.message : 'Could not create the template.'),
      )
      .finally(() => setSaving(false));
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>New template</CardTitle>
        <CardDescription>Firm admins can add document templates for the firm library.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="template-name">Name</Label>
            <Input
              id="template-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Revocable living trust"
              autoComplete="off"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="template-type">Type</Label>
            <select
              id="template-type"
              value={templateType}
              onChange={(event) => setTemplateType(event.target.value)}
              className="min-h-[44px] w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
            >
              {TEMPLATE_TYPES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="template-pack">Pack ID (optional)</Label>
            <Input
              id="template-pack"
              value={packId}
              onChange={(event) => setPackId(event.target.value)}
              placeholder="Links this template to a content pack"
              autoComplete="off"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="template-version">Version</Label>
            <Input
              id="template-version"
              value={version}
              onChange={(event) => setVersion(event.target.value)}
              placeholder="1.0"
              autoComplete="off"
            />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="template-content">Content</Label>
            <textarea
              id="template-content"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              rows={10}
              placeholder="Template body with {{placeholders}}…"
              className="flex w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 font-mono text-sm text-lf-text shadow-lf-subtle placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
            />
          </div>
          <div className="sm:col-span-2">
            {formError && (
              <p role="alert" className="mb-3 text-sm text-lf-error-700">
                {formError}
              </p>
            )}
            <Button type="submit" disabled={saving} className="min-h-[44px]">
              {saving ? 'Creating…' : 'Create template'}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

/** Firm template library: list, inspect content, and create (firm_admin only). */
export default function FirmTemplatesPage(): React.JSX.Element {
  const [templates, setTemplates] = useState<DocumentTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<DocumentTemplate | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    firmApi
      .listTemplates()
      .then((result) =>
        setTemplates(
          [...result].sort(
            (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
          ),
        ),
      )
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openDetail = (template: DocumentTemplate) => {
    setSelected(template);
    setDetailLoading(true);
    setDetailError(null);
    firmApi
      .getTemplate(template.id)
      .then(setSelected)
      .catch((err: unknown) =>
        setDetailError(err instanceof Error ? err.message : 'Could not load the template.'),
      )
      .finally(() => setDetailLoading(false));
  };

  const handleDelete = (template: DocumentTemplate) => {
    setDeleteError(null);
    setDeletingId(template.id);
    firmApi
      .deleteTemplate(template.id)
      .then(() => {
        setTemplates((prev) => prev.filter((t) => t.id !== template.id));
        setConfirmDeleteId(null);
        if (selected?.id === template.id) setSelected(null);
      })
      .catch((err: unknown) =>
        setDeleteError(
          err instanceof Error ? err.message : 'Could not delete the template.',
        ),
      )
      .finally(() => setDeletingId(null));
  };

  if (loading) return <LoadingState lines={6} label="Loading templates…" />;
  if (error != null) {
    if (error instanceof ApiError && error.isPermissionDenied) {
      return (
        <EmptyState
          title="Templates need firm admin access"
          description="Only firm admins can view and manage document templates."
        />
      );
    }
    return <ErrorState error={error} onRetry={load} />;
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Templates</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          Document template library — versions, content packs, and content.
        </p>
      </div>

      {templates.length === 0 ? (
        <EmptyState
          title="No templates yet"
          description="Firm admins can create the first template below."
        />
      ) : (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <caption className="sr-only">Document templates</caption>
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
                  <th scope="col" className="px-4 py-3 font-semibold">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {templates.map((template) => (
                  <tr key={template.id} className="border-t border-lf-border">
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        onClick={() => openDetail(template)}
                        className="min-h-[44px] font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
                      >
                        {template.name}
                      </button>
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">
                      {formatStatus(template.template_type)}
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">{template.version}</td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">
                      {template.pack_id ?? '—'}
                    </td>
                    <td className="px-4 py-3 text-sm text-lf-text-muted">
                      {formatDateTime(template.updated_at)}
                    </td>
                    <td className="px-4 py-3 text-sm">
                      {confirmDeleteId === template.id ? (
                        <span className="flex flex-wrap items-center gap-2">
                          <Button
                            type="button"
                            size="sm"
                            variant="destructive"
                            className="min-h-[44px]"
                            disabled={deletingId === template.id}
                            onClick={() => handleDelete(template)}
                          >
                            {deletingId === template.id ? 'Deleting…' : 'Confirm delete'}
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            className="min-h-[44px]"
                            disabled={deletingId === template.id}
                            onClick={() => setConfirmDeleteId(null)}
                          >
                            Cancel
                          </Button>
                        </span>
                      ) : (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="min-h-[44px]"
                          onClick={() => {
                            setDeleteError(null);
                            setConfirmDeleteId(template.id);
                          }}
                        >
                          Delete
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {deleteError && (
        <p role="alert" className="text-sm text-lf-error-700">
          {deleteError}
        </p>
      )}

      <CreateTemplateForm
        onCreated={(template) => setTemplates((prev) => [template, ...prev])}
      />

      <Modal
        open={selected != null}
        onClose={() => setSelected(null)}
        title={selected?.name ?? 'Template'}
        description={
          selected
            ? `${formatStatus(selected.template_type)} · v${selected.version}`
            : undefined
        }
      >
        {detailLoading && <LoadingState lines={4} label="Loading template…" />}
        {detailError && <p role="alert" className="text-sm text-lf-error-700">{detailError}</p>}
        {selected && !detailLoading && (
          <div className="space-y-3">
            <p className="text-sm text-lf-text-muted">
              Pack: {selected.pack_id ?? '—'} · Updated {formatDateTime(selected.updated_at)}
            </p>
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lf-control bg-lf-canvas-subtle p-4 font-mono text-sm text-lf-text">
              {selected.content}
            </pre>
          </div>
        )}
      </Modal>
    </div>
  );
}
