'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Paperclip } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { MatterSelect, useMatters } from '@/components/family/matter-context';
import { validateFile } from '@/components/files/upload-button';
import { api, fileDownloadUrl, type FundingTask, type FundingTaskStatus } from '@/lib/api-client';
import { formatDateTime, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

const STATUSES: FundingTaskStatus[] = ['pending', 'in_progress', 'complete'];

interface TaskCardProps {
  task: FundingTask;
  updating: boolean;
  uploadingEvidence: boolean;
  error: string | null;
  onStatusChange: (task: FundingTask, status: FundingTaskStatus) => void;
  onEvidence: (task: FundingTask, file: File) => void;
}

function TaskCard({
  task,
  updating,
  uploadingEvidence,
  error,
  onStatusChange,
  onEvidence,
}: TaskCardProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const handleFilePick = (files: FileList | null) => {
    const file = files?.[0];
    if (file) onEvidence(task, file);
    if (inputRef.current) inputRef.current.value = '';
  };

  const handleViewEvidence = () => {
    if (!task.evidence_file_id) return;
    setDownloadError(null);
    api
      .getDownloadToken(task.evidence_file_id)
      .then((response) => {
        const anchor = document.createElement('a');
        anchor.href = fileDownloadUrl(task.evidence_file_id ?? '', response.token);
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      })
      .catch((err: unknown) =>
        setDownloadError(err instanceof Error ? err.message : 'Could not open the evidence file.'),
      );
  };

  return (
    <Card>
      <CardContent className="space-y-3 p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-lf-text">{task.title}</p>
            {task.description && (
              <p className="mt-1 text-sm text-lf-text-muted">{task.description}</p>
            )}
          </div>
          <span
            className={cn(
              'shrink-0 rounded-full px-2.5 py-1 text-xs font-medium',
              statusBadgeClass(task.status),
            )}
          >
            {formatStatus(task.status)}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={`status-${task.id}`} className="text-sm font-medium text-lf-text">
            Status
          </label>
          <select
            id={`status-${task.id}`}
            value={task.status}
            disabled={updating}
            onChange={(event) => onStatusChange(task, event.target.value as FundingTaskStatus)}
            className="h-10 rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 disabled:opacity-60"
          >
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {formatStatus(status)}
              </option>
            ))}
          </select>
          {updating && <span className="text-xs text-lf-text-subtle">Saving…</span>}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={uploadingEvidence}
            onClick={() => inputRef.current?.click()}
          >
            <Paperclip className="size-4" aria-hidden="true" />
            {uploadingEvidence
              ? 'Uploading…'
              : task.evidence_file_id
                ? 'Replace evidence'
                : 'Upload evidence'}
          </Button>
          <input
            ref={inputRef}
            type="file"
            className="sr-only"
            aria-label={`Upload evidence for ${task.title}`}
            onChange={(event) => handleFilePick(event.target.files)}
          />
          {task.evidence_file_id && (
            <Button type="button" variant="outline" size="sm" onClick={handleViewEvidence}>
              View evidence
            </Button>
          )}
          <p className="w-full text-xs text-lf-text-subtle">
            Evidence is usually a statement, deed, or confirmation showing the step is done.
          </p>
        </div>

        {error && (
          <p role="alert" className="text-sm text-lf-error-700">
            {error}
          </p>
        )}
        {downloadError && (
          <p role="alert" className="text-sm text-lf-error-700">
            {downloadError}
          </p>
        )}
        <p className="text-xs text-lf-text-subtle">Updated {formatDateTime(task.updated_at)}</p>
      </CardContent>
    </Card>
  );
}

/** Family funding — task list with status updates and evidence uploads. */
export default function FamilyFundingPage(): React.JSX.Element {
  const {
    matters,
    loading: mattersLoading,
    error: mattersError,
    reload: reloadMatters,
  } = useMatters();
  const [matterId, setMatterId] = useState<string | null>(null);
  const [tasks, setTasks] = useState<FundingTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [updatingId, setUpdatingId] = useState<string | null>(null);
  const [uploadingId, setUploadingId] = useState<string | null>(null);
  const [taskErrors, setTaskErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);

  const loadTasks = useCallback((id: string) => {
    setLoading(true);
    setError(null);
    api
      .listFundingTasks(id)
      .then(setTasks)
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!matterId && matters.length > 0) {
      setMatterId(matters[0]?.id ?? null);
    }
  }, [matters, matterId]);

  useEffect(() => {
    if (matterId) loadTasks(matterId);
  }, [matterId, loadTasks]);

  const setTaskError = (taskId: string, message: string) =>
    setTaskErrors((prev) => ({ ...prev, [taskId]: message }));

  const handleStatusChange = (task: FundingTask, status: FundingTaskStatus) => {
    setUpdatingId(task.id);
    setNotice(null);
    api
      .updateFundingTask(task.id, { status })
      .then((updated) => {
        setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
        setNotice(`“${task.title}” marked ${formatStatus(status).toLowerCase()}.`);
      })
      .catch((err: unknown) =>
        setTaskError(task.id, err instanceof Error ? err.message : 'Could not save the status.'),
      )
      .finally(() => setUpdatingId(null));
  };

  const handleEvidence = (task: FundingTask, file: File) => {
    if (!matterId) return;
    const validationError = validateFile(file);
    if (validationError) {
      setTaskError(task.id, validationError);
      return;
    }
    setUploadingId(task.id);
    setNotice(null);
    api
      .requestIntent(matterId, {
        filename: file.name,
        mime_type: file.type,
        size_bytes: file.size,
        purpose: 'evidence',
      })
      .then((intent) => api.completeUpload(intent.upload_id, file))
      .then((completed) =>
        api.updateFundingTask(task.id, {
          status: task.status,
          evidence_file_id: completed.id,
        }),
      )
      .then((updated) => {
        setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
        setNotice(`Evidence attached to “${task.title}”.`);
      })
      .catch((err: unknown) =>
        setTaskError(
          task.id,
          err instanceof Error ? err.message : 'Could not upload the evidence.',
        ),
      )
      .finally(() => setUploadingId(null));
  };

  const completed = tasks.filter((t) => t.status === 'complete').length;

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">Funding</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Funding moves your assets into the plan — retitling accounts, updating beneficiaries. Work
          through the tasks and attach evidence as you finish each one.
        </p>
      </div>

      {mattersLoading && <LoadingState lines={3} label="Loading your matters…" />}
      {mattersError != null && !mattersLoading ? (
        <ErrorState error={mattersError} onRetry={reloadMatters} />
      ) : null}
      {!mattersLoading && !mattersError && matters.length === 0 && (
        <EmptyState
          title="No matters yet"
          description="Your funding tasks will appear here once your firm starts a plan for your household."
        />
      )}

      {!mattersLoading && !mattersError && matters.length > 0 && (
        <MatterSelect matters={matters} value={matterId} onChange={setMatterId} />
      )}

      {matterId && loading && <LoadingState lines={4} label="Loading funding tasks…" />}
      {error != null && matterId != null && !loading ? (
        <ErrorState error={error} onRetry={() => loadTasks(matterId)} />
      ) : null}

      {notice && (
        <p
          role="status"
          className="rounded-lf-control border border-lf-success-600 bg-lf-success-50 px-4 py-3 text-sm text-lf-success-700"
        >
          {notice}
        </p>
      )}

      {matterId && !loading && !error && (
        <section aria-label="Funding tasks" className="space-y-4">
          {tasks.length > 0 && (
            <p className="text-sm text-lf-text-muted">
              {completed} of {tasks.length} {tasks.length === 1 ? 'task' : 'tasks'} complete.
            </p>
          )}
          {tasks.length === 0 ? (
            <EmptyState
              title="No funding tasks yet"
              description="Your firm will generate funding tasks for your matter — check back soon."
            />
          ) : (
            tasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                updating={updatingId === task.id}
                uploadingEvidence={uploadingId === task.id}
                error={taskErrors[task.id] || null}
                onStatusChange={handleStatusChange}
                onEvidence={handleEvidence}
              />
            ))
          )}
        </section>
      )}
    </div>
  );
}
