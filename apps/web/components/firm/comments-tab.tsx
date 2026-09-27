'use client';

import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { firmApi, type MatterComment } from '@/lib/api-client';
import { formatDateTime } from '@/lib/format';

function CommentCard({ comment }: { comment: MatterComment }) {
  return (
    <Card>
      <CardContent className="space-y-1.5 p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-sm font-semibold text-lf-text">Firm team</p>
          <time className="text-xs text-lf-text-subtle">{formatDateTime(comment.created_at)}</time>
        </div>
        <p className="whitespace-pre-wrap text-sm text-lf-text">{comment.body}</p>
      </CardContent>
    </Card>
  );
}

/** Comments tab: internal thread plus a composer. */
export function CommentsTab({ matterId }: { matterId: string }): React.JSX.Element {
  const [comments, setComments] = useState<MatterComment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [body, setBody] = useState('');
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    firmApi
      .listComments(matterId)
      .then((result) =>
        setComments(
          [...result].sort(
            (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
          ),
        ),
      )
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, [matterId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = body.trim();
    if (trimmed.length === 0) {
      setFormError('Write a comment before posting.');
      return;
    }
    if (trimmed.length > 5000) {
      setFormError('Comments are limited to 5000 characters.');
      return;
    }
    setSaving(true);
    setFormError(null);
    firmApi
      .createComment(matterId, { body: trimmed })
      .then((comment) => {
        setComments((prev) => [...prev, comment]);
        setBody('');
      })
      .catch((err: unknown) =>
        setFormError(err instanceof Error ? err.message : 'Could not post the comment.'),
      )
      .finally(() => setSaving(false));
  };

  if (loading) return <LoadingState lines={4} label="Loading comments…" />;
  if (error != null) return <ErrorState error={error} onRetry={load} />;

  return (
    <div className="space-y-6">
      <Card>
        <CardContent className="p-5">
          <form onSubmit={handleSubmit} className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="comment-body">Add a comment</Label>
              <textarea
                id="comment-body"
                value={body}
                onChange={(event) => setBody(event.target.value)}
                rows={3}
                maxLength={5000}
                placeholder="Share context, questions, or next steps for the team…"
                className="flex w-full rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
              />
            </div>
            {formError && (
              <p role="alert" className="text-sm text-lf-error-700">
                {formError}
              </p>
            )}
            <Button type="submit" disabled={saving}>
              {saving ? 'Posting…' : 'Post comment'}
            </Button>
          </form>
        </CardContent>
      </Card>

      {comments.length === 0 ? (
        <EmptyState
          title="No comments yet"
          description="Be the first to leave a note on this matter. Comments are visible to the firm's team."
        />
      ) : (
        <ul className="space-y-3">
          {comments.map((comment) => (
            <li key={comment.id}>
              <CommentCard comment={comment} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
