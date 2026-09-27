'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { firmApi, type FundingTask } from '@/lib/api-client';
import { formatDateTime, formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

function TaskRow({ task }: { task: FundingTask }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-lf-text">{task.title}</p>
        {task.description && (
          <p className="mt-0.5 text-sm text-lf-text-muted">{task.description}</p>
        )}
        <p className="mt-0.5 text-xs text-lf-text-subtle">
          Updated {formatDateTime(task.updated_at)}
          {task.evidence_file_id ? ' · evidence attached' : ''}
        </p>
      </div>
      <span
        className={cn(
          'shrink-0 rounded-full px-2.5 py-1 text-xs font-medium',
          statusBadgeClass(task.status),
        )}
      >
        {formatStatus(task.status)}
      </span>
    </li>
  );
}

/** Firm funding tab — generate tasks for the matter and track their status. */
export function FundingTab({ matterId }: { matterId: string }): React.JSX.Element {
  const [tasks, setTasks] = useState<FundingTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [generating, setGenerating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const loadTasks = useCallback(() => {
    setLoading(true);
    setError(null);
    firmApi
      .listFundingTasks(matterId)
      .then(setTasks)
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, [matterId]);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  const handleGenerate = () => {
    setGenerating(true);
    setNotice(null);
    firmApi
      .generateFundingTasks(matterId)
      .then((generated) => {
        setTasks(generated);
        setNotice(
          `${generated.length} funding ${generated.length === 1 ? 'task' : 'tasks'} generated for this matter.`,
        );
      })
      .catch((err: unknown) =>
        setNotice(
          err instanceof Error
            ? `Could not generate tasks: ${err.message}`
            : 'Could not generate tasks.',
        ),
      )
      .finally(() => setGenerating(false));
  };

  if (loading) return <LoadingState lines={4} label="Loading funding tasks…" />;
  if (error != null) return <ErrorState error={error} onRetry={loadTasks} />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-lf-text-muted">
          Funding tasks track moving assets into the plan — retitling accounts, updating
          beneficiaries, and the evidence for each step.
        </p>
        <Button type="button" onClick={handleGenerate} disabled={generating}>
          {generating ? 'Generating…' : 'Generate funding tasks'}
        </Button>
      </div>

      {notice && (
        <p role="status" className="text-sm text-lf-text-muted">
          {notice}
        </p>
      )}

      {tasks.length === 0 ? (
        <EmptyState
          title="No funding tasks"
          description="Generate funding tasks from the jurisdiction pack to give the client a checklist of funding steps."
        />
      ) : (
        <Card>
          <CardContent className="p-0">
            <ul className="divide-y divide-lf-border">
              {tasks.map((task) => (
                <TaskRow key={task.id} task={task} />
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
