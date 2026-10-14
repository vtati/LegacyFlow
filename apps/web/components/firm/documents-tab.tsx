'use client';

import { useCallback, useEffect, useState } from 'react';
import { Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Modal } from '@/components/ui/modal';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { WATERMARK_NOTICE, WatermarkNotice } from '@/components/documents/watermark-notice';
import {
  ApiError,
  apiFetchBlob,
  firmApi,
  type AmendmentRequest,
  type DraftFormat,
  type DocumentPackage,
  type DocumentTemplate,
} from '@/lib/api-client';
import { formatDateTime, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

const FORMATS: DraftFormat[] = ['pdf', 'docx'];

interface PackageCardProps {
  matterId: string;
  pkg: DocumentPackage;
  templates: DocumentTemplate[];
}

function PackageCard({ pkg, templates }: PackageCardProps) {
  const [format, setFormat] = useState<DraftFormat>('pdf');
  const [generating, setGenerating] = useState(false);
  const [releasing, setReleasing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [releaseError, setReleaseError] = useState<string | null>(null);
  const [confirmRelease, setConfirmRelease] = useState(false);

  const templateName =
    templates.find((t) => t.id === pkg.template_id)?.name ?? pkg.template_id;

  const downloadBlob = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  const handleGenerateDraft = () => {
    setGenerating(true);
    setNotice(null);
    firmApi
      .generateDraft(pkg.id, format)
      .then(async ({ versions }) => {
        const version = versions.find((v) => v.file_format === format);
        if (!version) {
          throw new Error(`No ${format} version was generated.`);
        }
        // Download the generated version via its download_url.
        const path = version.download_url.replace(/^\/v1/, '');
        const { blob, filename } = await apiFetchBlob(path, { method: 'GET' });
        downloadBlob(blob, filename);
        setNotice(`Draft downloaded: ${filename} (${WATERMARK_NOTICE}).`);
      })
      .catch((err: unknown) =>
        setNotice(
          err instanceof Error
            ? `Could not generate the draft: ${err.message}`
            : 'Could not generate the draft.',
        ),
      )
      .finally(() => setGenerating(false));
  };

  const handleRelease = () => {
    setConfirmRelease(false);
    setReleasing(true);
    setReleaseError(null);
    setNotice(null);
    firmApi
      .releaseProduction(pkg.id)
      .then(async ({ versions }) => {
        const version = versions.find((v) => v.file_format === format);
        if (!version) {
          throw new Error(`No ${format} final version was released.`);
        }
        const path = version.download_url.replace(/^\/v1/, '');
        const { blob, filename } = await apiFetchBlob(path, { method: 'GET' });
        downloadBlob(blob, filename);
        setNotice(`Final documents released and downloaded: ${filename}.`);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 409) {
          setReleaseError(
            'Final release requires attorney approval of the document release gate first.',
          );
        } else {
          setReleaseError(
            err instanceof Error
              ? `Could not release: ${err.message}`
              : 'Could not release the package.',
          );
        }
      })
      .finally(() => setReleasing(false));
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="text-base">
              Package created {formatDateTime(pkg.created_at)}
            </CardTitle>
            <CardDescription>{templateName}</CardDescription>
          </div>
          <span
            className={cn(
              'shrink-0 rounded-full px-2.5 py-1 text-xs font-medium',
              statusBadgeClass(pkg.status),
            )}
          >
            {formatStatus(pkg.status)}
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={`format-${pkg.id}`} className="text-sm font-medium text-lf-text">
            Draft format
          </label>
          <select
            id={`format-${pkg.id}`}
            value={format}
            onChange={(event) => setFormat(event.target.value as DraftFormat)}
            className="h-10 rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
          >
            {FORMATS.map((f) => (
              <option key={f} value={f}>
                {f.toUpperCase()}
              </option>
            ))}
          </select>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={generating}
            onClick={handleGenerateDraft}
          >
            <Download className="size-4" aria-hidden="true" />
            {generating ? 'Generating…' : 'Generate draft'}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={releasing}
            onClick={() => setConfirmRelease(true)}
          >
            {releasing ? 'Releasing…' : 'Release for production'}
          </Button>
        </div>
        <Modal
          open={confirmRelease}
          onClose={() => setConfirmRelease(false)}
          title="Release final documents?"
          description="This will generate the clean, final versions of the documents for production use. This action should only be taken after attorney approval."
        >
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setConfirmRelease(false)}>
              Cancel
            </Button>
            <Button type="button" onClick={handleRelease} disabled={releasing}>
              {releasing ? 'Releasing…' : 'Confirm release'}
            </Button>
          </div>
        </Modal>
        {notice && (
          <p role="status" className="text-sm text-lf-text-muted">
            {notice}
          </p>
        )}
        {releaseError && (
          <p
            role="alert"
            className="rounded-lf-control border border-lf-accent-600 bg-lf-accent-50 px-3 py-2 text-sm text-lf-accent-800"
          >
            {releaseError}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

/** Firm documents tab — templates, package creation, watermarked draft generation. */
export function DocumentsTab({ matterId }: { matterId: string }): React.JSX.Element {
  const [templates, setTemplates] = useState<DocumentTemplate[]>([]);
  const [packages, setPackages] = useState<DocumentPackage[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [downloadingPackage, setDownloadingPackage] = useState(false);
  const [packageError, setPackageError] = useState<string | null>(null);
  const [amendments, setAmendments] = useState<AmendmentRequest[]>([]);
  const [amendmentText, setAmendmentText] = useState('');
  const [requestingAmendment, setRequestingAmendment] = useState(false);
  const [amendmentError, setAmendmentError] = useState<string | null>(null);

  const loadAll = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      firmApi.listTemplates(),
      firmApi.listPackages(matterId),
      firmApi.listAmendmentRequests(matterId),
    ])
      .then(([templateList, packageList, amendmentList]) => {
        setTemplates(templateList);
        setPackages([...packageList].sort((a, b) => b.created_at.localeCompare(a.created_at)));
        setAmendments(amendmentList);
      })
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, [matterId]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const toggleTemplate = (id: string) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]));
  };

  const handleCreate = () => {
    if (selected.length === 0) {
      setFormError('Select at least one template to create a package.');
      return;
    }
    setCreating(true);
    setFormError(null);
    // The API creates one package per template; create a package for each
    // selected template so multi-select still works end to end.
    Promise.all(selected.map((templateId) => firmApi.createPackage(matterId, { template_id: templateId })))
      .then((created) => {
        setPackages((prev) =>
          [...created, ...prev].sort((a, b) => b.created_at.localeCompare(a.created_at)),
        );
        setSelected([]);
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 409) {
          setFormError('Document assembly is not yet enabled for this jurisdiction.');
        } else {
          setFormError(err instanceof Error ? err.message : 'Could not create the package.');
        }
      })
      .finally(() => setCreating(false));
  };

  if (loading) return <LoadingState lines={4} label="Loading documents…" />;
  if (error != null) return <ErrorState error={error} onRetry={loadAll} />;

  const releasedCount = packages.filter((p) => p.status === 'released').length;
  const allReleased = packages.length > 0 && releasedCount === packages.length;

  const handleDownloadNotaryPackage = () => {
    setDownloadingPackage(true);
    setPackageError(null);
    firmApi
      .downloadNotaryPackage(matterId)
      .then(({ blob, filename }) => {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
      })
      .catch((err: unknown) => {
        setPackageError(
          err instanceof ApiError && err.status === 409
            ? 'All packages must be released before building the notary package.'
            : err instanceof Error
              ? `Could not build the notary package: ${err.message}`
              : 'Could not build the notary package.',
        );
      })
      .finally(() => setDownloadingPackage(false));
  };

  return (
    <div className="space-y-6">
      <WatermarkNotice />

      <Card>
        <CardHeader>
          <CardTitle>Notary execution package</CardTitle>
          <CardDescription>
            Download a single PDF with the final documents and the Virginia execution
            checklist — ready to share with the notary. All packages must be released first.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-lf-text-muted">
            {releasedCount} of {packages.length} packages released.
          </p>
          {packageError && (
            <p role="alert" className="text-sm text-lf-error-700">
              {packageError}
            </p>
          )}
          <Button
            type="button"
            onClick={handleDownloadNotaryPackage}
            disabled={downloadingPackage || !allReleased}
          >
            <Download className="size-4" aria-hidden="true" />
            {downloadingPackage ? 'Building…' : 'Download notary package (PDF)'}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Request changes</CardTitle>
          <CardDescription>
            Life changes — new property, new beneficiaries, new wishes. Describe what changed
            and the attorney will review whether a codicil, trust amendment, or restatement is needed.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Label htmlFor="amendment-text">Describe what changed</Label>
          <textarea
            id="amendment-text"
            className="w-full rounded-md border border-lf-border bg-lf-canvas-elevated p-3 text-sm"
            rows={3}
            placeholder="e.g. We bought a new house at 123 Oak St. Please add it to the trust."
            value={amendmentText}
            onChange={(e) => setAmendmentText(e.target.value)}
          />
          {amendmentError && (
            <p role="alert" className="text-sm text-lf-error-700">
              {amendmentError}
            </p>
          )}
          <Button
            type="button"
            disabled={requestingAmendment || !amendmentText.trim()}
            onClick={() => {
              setRequestingAmendment(true);
              setAmendmentError(null);
              firmApi
                .createAmendmentRequest(matterId, {
                  description: amendmentText.trim(),
                  affected_template_types: [],
                })
                .then((created) => {
                  setAmendments((prev) => [created, ...prev]);
                  setAmendmentText('');
                })
                .catch((err: unknown) => {
                  setAmendmentError(
                    err instanceof ApiError ? err.message : 'Could not submit the request.',
                  );
                })
                .finally(() => setRequestingAmendment(false));
            }}
          >
            {requestingAmendment ? 'Submitting…' : 'Submit change request'}
          </Button>
          {amendments.length > 0 && (
            <div className="space-y-2 pt-2">
              <p className="text-sm font-medium">Change history</p>
              {amendments.map((a) => (
                <div key={a.id} className="rounded-md border border-lf-border p-3 text-sm">
                  <p>{a.description}</p>
                  <p className="mt-1 text-xs text-lf-text-muted">
                    {formatStatus(a.status)} · {formatDateTime(a.created_at)}
                  </p>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Templates</CardTitle>
          <CardDescription>
            Select templates from the jurisdiction pack to build a document package.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {templates.length === 0 ? (
            <EmptyState
              title="No templates available"
              description="No document templates are published for this matter's jurisdiction yet."
            />
          ) : (
            <ul className="space-y-2">
              {templates.map((template) => (
                <li key={template.id}>
                  <label className="flex cursor-pointer items-start gap-3 rounded-lf-control border border-lf-border px-4 py-3 hover:bg-lf-canvas-subtle">
                    <input
                      type="checkbox"
                      checked={selected.includes(template.id)}
                      onChange={() => toggleTemplate(template.id)}
                      className="mt-1 size-4 accent-lf-primary-700"
                    />
                    <span>
                      <span className="block text-sm font-medium text-lf-text">
                        {template.name}
                      </span>
                      <span className="block text-xs text-lf-text-subtle">
                        {template.template_type} · v{template.version}
                        {template.pack_id ? ` · pack ${template.pack_id}` : ''}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          <div className="space-y-1.5">
            <p className="text-sm text-lf-text-muted">
              One package is created per selected template.
            </p>
          </div>
          {formError && (
            <p role="alert" className="text-sm text-lf-error-700">
              {formError}
            </p>
          )}
          <Button type="button" onClick={handleCreate} disabled={creating}>
            {creating ? 'Creating…' : 'Create package'}
          </Button>
        </CardContent>
      </Card>

      <div className="space-y-4">
        <h2 className="text-base font-semibold text-lf-text">Packages</h2>
        {packages.length === 0 ? (
          <EmptyState
            title="No packages yet"
            description="Create a package above to generate watermarked draft documents for review."
          />
        ) : (
          packages.map((pkg) => (
            <PackageCard key={pkg.id} matterId={matterId} pkg={pkg} templates={templates} />
          ))
        )}
      </div>
    </div>
  );
}
