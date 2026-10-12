'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  FileUp,
  Mic,
  Pause,
  Pencil,
  Play,
  Scale,
  Send,
  ShieldCheck,
  Square,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { ProgressBar } from '@/components/family/progress-bar';
import {
  ApiError,
  api,
  type ConsistencyFinding,
  type ConsistencyReview,
  type InterviewCitation,
  type InterviewExtraction,
  type InterviewExtractionSummary,
  type InterviewProposal,
  type InterviewSession,
} from '@/lib/api-client';
import { cn } from '@/lib/utils';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  citations?: InterviewCitation[];
  refused?: boolean;
}

interface InterviewChatProps {
  matterId: string;
}

function humanizeKey(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (typeof item === 'string') return item;
        if (typeof item === 'object' && item !== null) {
          // Format {name, age} objects as "Name (age 7)" etc.
          const obj = item as Record<string, unknown>;
          const name = obj.name ?? obj.label ?? obj.value;
          const age = obj.age;
          if (name && age !== undefined) return `${name} (age ${age})`;
          if (name) return String(name);
          return Object.values(obj)
            .filter((v) => v !== null && v !== undefined && v !== '')
            .join(' ');
        }
        return String(item);
      })
      .join('; ');
  }
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function parseEditedValue(raw: string, original: unknown): unknown {
  const trimmed = raw.trim();
  if (typeof original === 'string' && !(trimmed.startsWith('{') || trimmed.startsWith('['))) {
    return raw;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    return raw;
  }
}

function provenanceLabel(proposal: InterviewProposal): string | null {
  if (proposal.source === 'document_extracted') {
    return proposal.source_page != null
      ? `From your document (page ${proposal.source_page})`
      : 'From your document';
  }
  return null;
}

/** One verification card: confirm, edit, or reject a proposed field value. */
function ProposalCard({
  proposal,
  matterId,
  onDecided,
}: {
  proposal: InterviewProposal;
  matterId: string;
  onDecided: (proposal: InterviewProposal) => void;
}): React.JSX.Element {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(() => formatValue(proposal.proposed_value));
  const [busy, setBusy] = useState<'confirm' | 'correct' | 'reject' | null>(null);
  const [error, setError] = useState<string | null>(null);

  return (
    <Card className="lf-proposal border-lf-border bg-white shadow-none">
      <CardContent className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-2">
          <p className="text-sm font-semibold text-lf-text">{humanizeKey(proposal.question_key)}</p>
          {proposal.confidence != null && (
            <span className="shrink-0 rounded-full bg-lf-canvas-subtle px-2 py-0.5 text-xs text-lf-text-muted">
              {Math.round(proposal.confidence * 100)}% confident
            </span>
          )}
        </div>
        {editing ? (
          <div className="space-y-2">
            <Label htmlFor={`edit-${proposal.id}`}>Corrected value</Label>
            <Input
              id={`edit-${proposal.id}`}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={busy != null}
            />
          </div>
        ) : (
          <div className="space-y-1.5">
            <p className="text-xs font-medium uppercase tracking-wide text-lf-text-muted">Proposed</p>
            <p className="rounded-lf-control border border-lf-border bg-lf-primary-50 px-3 py-2 text-sm text-lf-text">
              {formatValue(proposal.proposed_value)}
            </p>
          </div>
        )}
        {proposal.source_snippet && (
          <p className="text-xs italic text-lf-text-muted">
            {provenanceLabel(proposal) ?? 'You said'}: “{proposal.source_snippet}”
          </p>
        )}
        {proposal.source === 'document_extracted' && !proposal.source_snippet && (
          <p className="text-xs text-lf-text-muted">{provenanceLabel(proposal)}</p>
        )}
        {error && <p className="text-xs text-lf-error-600">{error}</p>}
        <ProposalActions
          proposal={proposal}
          matterId={matterId}
          editing={editing}
          draft={draft}
          busy={busy}
          setBusy={setBusy}
          setError={setError}
          onToggleEdit={() => {
            setEditing((v) => !v);
            setDraft(formatValue(proposal.proposed_value));
          }}
          onDecided={onDecided}
        />
      </CardContent>
    </Card>
  );
}

/** Action row for a proposal card; separated so hooks stay simple. */
function ProposalActions({
  proposal,
  matterId,
  editing,
  draft,
  busy,
  setBusy,
  setError,
  onToggleEdit,
  onDecided,
}: {
  proposal: InterviewProposal;
  matterId: string;
  editing: boolean;
  draft: string;
  busy: 'confirm' | 'correct' | 'reject' | null;
  setBusy: (b: 'confirm' | 'correct' | 'reject' | null) => void;
  setError: (e: string | null) => void;
  onToggleEdit: () => void;
  onDecided: (proposal: InterviewProposal, matterId: string) => void;
}): React.JSX.Element {
  const run = async (kind: 'confirm' | 'correct' | 'reject') => {
    setBusy(kind);
    setError(null);
    try {
      const updated =
        kind === 'confirm'
          ? await api.confirmInterviewProposal(matterId, proposal.id)
          : kind === 'correct'
            ? await api.correctInterviewProposal(
                matterId,
                proposal.id,
                parseEditedValue(draft, proposal.proposed_value),
              )
            : await api.rejectInterviewProposal(matterId, proposal.id);
      onDecided(updated, matterId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
      {editing ? (
        <>
          <Button size="sm" disabled={busy != null} onClick={() => void run('correct')} className="h-11 bg-lf-primary-900 text-white hover:bg-lf-primary-800 sm:h-8">
            <Check className="size-4" aria-hidden="true" /> Save correction
          </Button>
          <Button size="sm" variant="outline" disabled={busy != null} onClick={onToggleEdit} className="h-11 sm:h-8">
            Cancel
          </Button>
        </>
      ) : (
        <>
          <Button size="sm" disabled={busy != null} onClick={() => void run('confirm')} className="h-11 bg-lf-primary-900 text-white hover:bg-lf-primary-800 sm:h-8">
            <Check className="size-4" aria-hidden="true" />
            {busy === 'confirm' ? 'Confirming…' : 'Confirm'}
          </Button>
          <Button size="sm" variant="outline" disabled={busy != null} onClick={onToggleEdit} className="h-11 sm:h-8">
            <Pencil className="size-4" aria-hidden="true" /> Edit
          </Button>
          <Button size="sm" variant="ghost" disabled={busy != null} onClick={() => void run('reject')} className="h-11 text-lf-error-700 hover:bg-lf-error-50 hover:text-lf-error-800 sm:h-8">
            <X className="size-4" aria-hidden="true" />
            {busy === 'reject' ? 'Rejecting…' : 'Reject'}
          </Button>
        </>
      )}
    </div>
  );
}

/** The AI interviewer: chat thread, verification cards, coverage checklist. */
export function InterviewChat({ matterId }: InterviewChatProps): React.JSX.Element {
  const [phase, setPhase] = useState<'loading' | 'ready' | 'error' | 'disabled'>('loading');
  const [error, setError] = useState<string | null>(null);
  const [session, setSession] = useState<InterviewSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [proposals, setProposals] = useState<InterviewProposal[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [recording, setRecording] = useState(false);
  const [dictating, setDictating] = useState(false);
  const [checklistOpen, setChecklistOpen] = useState(false);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [extractions, setExtractions] = useState<InterviewExtractionSummary[]>([]);
  const [extracting, setExtracting] = useState(false);
  const [extractionMsg, setExtractionMsg] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [review, setReview] = useState<ConsistencyReview | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const refreshSession = useCallback(
    async (sessionId: string) => {
      const [fresh, open] = await Promise.all([
        api.getInterviewSession(matterId, sessionId),
        api.listInterviewProposals(matterId, sessionId),
      ]);
      setSession(fresh);
      setProposals(open);
    },
    [matterId],
  );

  const refreshExtractions = useCallback(
    async (sessionId: string) => {
      try {
        setExtractions(await api.listInterviewExtractions(matterId, sessionId));
      } catch {
        // Extractions are informational; the interview still works without them.
      }
    },
    [matterId],
  );

  const bootstrap = useCallback(async () => {
    setPhase('loading');
    setError(null);
    try {
      const sets = await api.getQuestionSets(matterId);
      const setIds = Object.keys(sets.question_sets ?? {});
      if (setIds.length === 0) throw new Error('No question sets available.');
      // One session per question set; start with the first set (household).
      const started = await api.startInterviewSession(matterId, setIds[0]);
      setSession(started);
      const summaries = await api.listInterviewTurns(matterId, started.id);
      // API returns newest-first; reverse to chronological for display.
      // Use summary text directly — avoids N+1 individual turn fetches.
      // Full detail (citations) loads lazily only when user expands them.
      const recent = summaries.slice(-20).reverse();
      const hydrated: ChatMessage[] = recent.map((s) => ({
        id: s.id,
        role: s.role as 'user' | 'assistant',
        text: s.summary,
      }));
      setMessages(hydrated);
      const open = await api.listInterviewProposals(matterId, started.id);
      setProposals(open);
      await refreshExtractions(started.id);
      setPhase('ready');
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setPhase('disabled');
        setError(err.message);
      } else {
        setPhase('error');
        setError(err instanceof ApiError ? err.message : 'Something went wrong.');
      }
    }
  }, [matterId]);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  // useLayoutEffect (not useEffect): runs synchronously after DOM mutations
  // but before the browser paints. This guarantees the scroll position is
  // correct before the user sees anything — no flash of wrong position,
  // no race with paint. 'auto' (instant) because browsers cancel smooth
  // scrolls during page load.
  useLayoutEffect(() => {
    const el = threadRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending || session == null) return;
    if (session.status === 'paused') {
      setError('Resume the interview before sending messages.');
      return;
    }
    if (session.status === 'completed' || session.status === 'abandoned') return;
    setSending(true);
    setError(null);
    const userMsg: ChatMessage = {
      id: `local-${Date.now()}`,
      role: 'user',
      text,
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    try {
      const idempotencyKey =
        typeof crypto !== 'undefined' && 'randomUUID' in crypto
          ? crypto.randomUUID()
          : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const turn = await api.postInterviewTurn(matterId, session.id, text, idempotencyKey);
      setMessages((prev) => [
        ...prev,
        {
          id: turn.id,
          role: 'assistant',
          text: turn.reply,
          citations: turn.citations,
          refused: turn.refused,
        },
      ]);
      await refreshSession(session.id);
    } catch (err) {
      setMessages((prev) => prev.filter((m) => m.id !== userMsg.id));
      setInput(text);
      setError(err instanceof ApiError ? err.message : 'Something went wrong.');
    } finally {
      setSending(false);
    }
  }, [input, sending, session, matterId, refreshSession]);

  const toggleRecording = useCallback(async () => {
    if (recording) {
      mediaRecorderRef.current?.stop();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        setRecording(false);
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        if (blob.size === 0) return;
        setDictating(true);
        try {
          const { transcript } = await api.dictateInterview(matterId, blob);
          // Transcript lands in the composer for review — never auto-sent.
          setInput((prev) => (prev ? `${prev} ${transcript}` : transcript));
        } catch (err) {
          setError(err instanceof ApiError ? err.message : 'Dictation failed.');
        } finally {
          setDictating(false);
        }
      };
      mediaRecorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      setError('Microphone access was denied. You can still type your answers.');
    }
  }, [recording, matterId]);

  const updateStatus = useCallback(
    async (status: 'paused' | 'in_progress' | 'abandoned' | 'completed') => {
      if (session == null) return;
      setSessionBusy(true);
      setError(null);
      try {
        const updated = await api.updateInterviewSession(matterId, session.id, status);
        setSession(updated);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Something went wrong.');
      } finally {
        setSessionBusy(false);
      }
    },
    [session, matterId],
  );

  const onProposalDecided = useCallback(
    () => {
      if (session) void refreshSession(session.id);
    },
    [session, refreshSession],
  );

  const uploadDocument = useCallback(
    async (file: File) => {
      if (session == null || extracting) return;
      setExtracting(true);
      setExtractionMsg(null);
      setError(null);
      try {
        const result: InterviewExtraction = await api.extractInterviewDocument(
          matterId,
          session.id,
          file,
        );
        if (result.status === 'no_readable_text') {
          setExtractionMsg(
            'We could not read any text from that file — not even with scan recognition. Please type the details instead.',
          );
        } else if (result.status === 'failed') {
          setExtractionMsg('We could not extract this file. Please try a PDF or Word document.');
        } else {
          const parts: string[] = [];
          if (result.proposals.length > 0)
            parts.push(`${result.proposals.length} value${result.proposals.length === 1 ? '' : 's'} proposed`);
          if (result.skipped_existing.length > 0)
            parts.push(`${result.skipped_existing.length} already have a value awaiting your review`);
          setExtractionMsg(
            `Extracted “${result.filename}”${result.ocr_used ? ' from a scan (please double-check the values)' : ''}. ${parts.length > 0 ? parts.join('; ') + '.' : 'Nothing new to verify.'}`,
          );
        }
        await refreshExtractions(session.id);
        await refreshSession(session.id);
      } catch (err) {
        setExtractionMsg(err instanceof ApiError ? err.message : 'Document upload failed.');
      } finally {
        setExtracting(false);
      }
    },
    [session, extracting, matterId, refreshExtractions, refreshSession],
  );

  const onFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = '';
      if (file) void uploadDocument(file);
    },
    [uploadDocument],
  );

  const runReview = useCallback(async () => {
    if (session == null || reviewing) return;
    setReviewing(true);
    setError(null);
    try {
      const result = await api.runInterviewConsistencyReview(matterId, session.id);
      setReview(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Consistency check failed.');
    } finally {
      setReviewing(false);
    }
  }, [session, reviewing, matterId]);

  if (phase === 'loading') return <LoadingState lines={6} label="Starting your interview…" />;
  if (phase === 'error')
    return <ErrorState error={error ?? 'Something went wrong.'} onRetry={() => void bootstrap()} />;
  if (phase === 'disabled' || session == null) {
    return (
      <EmptyState
        title="AI interviewer not available"
        description={
          error ?? 'The AI interviewer is not enabled for your firm yet. You can continue with the guided intake form.'
        }
      />
    );
  }

  const paused = session.status === 'paused';
  const done = session.status === 'completed' || session.status === 'abandoned';
  const requiredMissing = session.checklist.filter((c) => c.required && !c.answered);

  return (
    <div className="mx-auto max-w-6xl space-y-4 px-4 lg:px-0">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-lf-serif text-2xl font-semibold tracking-tight text-lf-text">AI interview</h1>
          <p className="mt-1 text-sm text-lf-text-muted">
            Chat with the intake assistant. Review every proposed value before it becomes part of
            your file — nothing here is legal advice.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={cn(
              'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
              paused && 'bg-lf-warning-100 text-lf-warning-700',
              !paused && !done && 'bg-lf-success-100 text-lf-success-800',
              done && 'bg-lf-canvas-subtle text-lf-text-muted',
            )}
          >
            {session.status === 'in_progress' ? 'In progress' : session.status}
          </span>
          {!done && (
            <>
              <Button
                variant="outline"
                size="sm"
                disabled={sessionBusy}
                onClick={() => void updateStatus(paused ? 'in_progress' : 'paused')}
              >
                {paused ? <Play className="size-4" aria-hidden="true" /> : <Pause className="size-4" aria-hidden="true" />}
                {paused ? 'Resume' : 'Pause'}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={sessionBusy}
                onClick={() => void updateStatus('completed')}
              >
                <ShieldCheck className="size-4" aria-hidden="true" /> Finish
              </Button>
            </>
          )}
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-lf-control border border-lf-error-200 bg-lf-error-50 p-3 text-sm text-lf-error-700" role="alert">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{error}</span>
          <button
            type="button"
            className="ml-auto shrink-0 underline-offset-4 hover:underline"
            onClick={() => setError(null)}
          >
            Dismiss
          </button>
        </div>
      )}

      {paused && (
        <div className="rounded-lf-control border border-lf-warning-200 bg-lf-warning-50 p-3 text-sm text-lf-warning-700" role="status">
          This interview is paused. Your progress is saved — resume whenever you’re ready.
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        {/* Chat thread */}
        <Card className="border-lf-border bg-lf-canvas shadow-none lg:col-span-2">
          <CardContent className="flex h-[calc(100dvh-13rem)] flex-col p-0 lg:h-[60vh]">
            <div ref={threadRef} className="flex-1 space-y-6 overflow-y-auto px-4 py-6 sm:px-6" aria-live="polite">
              {messages.length === 0 && (
                <p className="text-sm text-lf-text-muted">
                  Say hello to begin. The assistant will guide you through each item, one question
                  at a time.
                </p>
              )}
              {messages.map((m) => (
                <div
                  key={m.id}
                  className={cn(
                    'lf-animate-slide-in flex',
                    m.role === 'user' ? 'justify-end' : 'justify-start gap-3',
                  )}
                >
                  {m.role === 'assistant' && (
                    <span
                      aria-hidden="true"
                      className="flex size-8 shrink-0 items-center justify-center rounded-full bg-lf-primary-900 text-xs font-semibold text-white"
                    >
                      AI
                    </span>
                  )}
                  <div
                    className={cn(
                      m.role === 'user'
                        ? 'max-w-full rounded-2xl rounded-br-sm bg-lf-primary-50 px-4 py-2.5 text-sm text-lf-text sm:max-w-[85%]'
                        : 'max-w-full sm:max-w-[68ch]',
                    )}
                  >
                    <p
                      className={cn(
                        'whitespace-pre-wrap',
                        m.role === 'assistant' && 'text-[15px] leading-relaxed text-lf-text',
                      )}
                    >
                      {m.text}
                    </p>
                    {m.citations && m.citations.length > 0 && (
                      <details className="mt-3 text-xs">
                        <summary className="cursor-pointer font-lf-serif text-sm text-lf-text-muted">
                          Sources ({m.citations.length})
                        </summary>
                        <ol className="mt-2 space-y-2 border-t border-lf-border pt-2">
                          {m.citations.map((c, i) => (
                            <li key={i} className="flex gap-2">
                              <span
                                aria-hidden="true"
                                className="shrink-0 font-medium text-lf-text-muted"
                              >
                                {i + 1}.
                              </span>
                              <span>
                                <span className="font-medium text-lf-text">{c.source}</span>
                                {c.section ? ` · ${c.section}` : ''}: {c.excerpt}
                              </span>
                            </li>
                          ))}
                        </ol>
                      </details>
                    )}
                  </div>
                </div>
              ))}
              {sending && (
                <div className="flex justify-start gap-3" aria-live="polite">
                  <span
                    aria-hidden="true"
                    className="flex size-8 shrink-0 items-center justify-center rounded-full bg-lf-primary-900 text-xs font-semibold text-white"
                  >
                    AI
                  </span>
                  <div className="flex items-center gap-1.5 py-3" role="status">
                    <span className="sr-only">The assistant is thinking…</span>
                    {[0, 1, 2].map((i) => (
                      <span
                        key={i}
                        aria-hidden="true"
                        className="lf-typing-dot"
                        style={{ animationDelay: `${i * 150}ms` }}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className="sticky bottom-0 border-t border-lf-border bg-lf-canvas px-4 pb-4 pt-3">
              <Label htmlFor="interview-composer" className="sr-only">
                Message the intake assistant
              </Label>
              <div className="lf-composer flex items-end gap-1 rounded-2xl border border-lf-border bg-white p-2 shadow-[0_8px_24px_rgb(15_23_42/0.08)]">
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  disabled={sending || done || paused || dictating}
                  onClick={() => void toggleRecording()}
                  aria-label={recording ? 'Stop dictation' : 'Dictate your answer'}
                  title="Dictate — the transcript appears here for your review before sending"
                  className={cn(
                    'size-11 shrink-0 text-lf-text-muted hover:text-lf-text',
                    recording && 'border-lf-error-400 text-lf-error-600',
                  )}
                >
                  {recording ? <Square className="size-4" aria-hidden="true" /> : <Mic className="size-4" aria-hidden="true" />}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  disabled={sending || done || paused || extracting}
                  onClick={() => fileInputRef.current?.click()}
                  aria-label="Attach a document"
                  title="Attach a PDF or Word document — the assistant extracts details for you to verify"
                  className="size-11 shrink-0 text-lf-text-muted hover:text-lf-text"
                >
                  <FileUp className="size-4" aria-hidden="true" />
                </Button>
                <textarea
                  id="interview-composer"
                  className="max-h-32 min-h-11 flex-1 resize-y border-0 bg-transparent px-2 py-2 text-sm text-lf-text placeholder:text-lf-text-muted focus-visible:outline-none"
                  placeholder={paused ? 'Resume the interview to continue…' : 'Type your answer…'}
                  value={input}
                  disabled={sending || done || paused}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault();
                      void send();
                    }
                  }}
                />
                <Button
                  type="button"
                  disabled={sending || done || paused || !input.trim()}
                  onClick={() => void send()}
                  className="size-11 shrink-0 bg-lf-primary-900 text-white hover:bg-lf-primary-800"
                >
                  <Send className="size-4" aria-hidden="true" />
                  <span className="sr-only">Send</span>
                </Button>
              </div>
              {(recording || dictating) && (
                <p className="mt-1 text-xs text-lf-text-muted" role="status">
                  {recording ? 'Recording… tap the microphone again to stop.' : 'Transcribing…'}
                </p>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Sidebar */}
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Progress</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <ProgressBar value={session.completeness_pct} label="Interview completeness" />
              <p className="text-xs text-lf-text-muted">
                {requiredMissing.length === 0
                  ? 'All required items are collected.'
                  : `${requiredMissing.length} required item${requiredMissing.length === 1 ? '' : 's'} still needed.`}
              </p>
              <button
                type="button"
                className="flex w-full items-center justify-between text-sm font-medium text-lf-text"
                onClick={() => setChecklistOpen((v) => !v)}
                aria-expanded={checklistOpen}
              >
                Coverage checklist
                <ChevronDown className={cn('size-4 transition-transform', checklistOpen && 'rotate-180')} aria-hidden="true" />
              </button>
              {checklistOpen && (
                <ul className="max-h-64 space-y-1 overflow-y-auto text-sm">
                  {session.checklist.map((item) => (
                    <li key={item.question_key} className="flex items-center gap-2">
                      <span
                        aria-hidden="true"
                        className={cn(
                          'inline-block size-2 shrink-0 rounded-full',
                          item.answered ? 'bg-lf-success-500' : item.has_open_proposal ? 'bg-lf-warning-500' : 'bg-lf-border',
                        )}
                      />
                      <span className={cn(!item.required && 'text-lf-text-muted')}>
                        {humanizeKey(item.question_key)}
                      </span>
                      {item.has_open_proposal && (
                        <span className="text-xs text-lf-warning-700">(awaiting review)</span>
                      )}
                      <span className="sr-only">{item.answered ? 'answered' : 'not answered'}</span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">
                Verify values{' '}
                {proposals.length > 0 && (
                  <span className="ml-1 rounded-full bg-lf-primary-100 px-2 py-0.5 text-xs font-medium text-lf-primary-700">
                    {proposals.length}
                  </span>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-lf-text-muted">
                The assistant proposes values from what you said. Confirm each one before it joins
                your file.
              </p>
              {proposals.length === 0 && (
                <p className="text-sm text-lf-text-muted">No values awaiting review.</p>
              )}
              {proposals.map((p) => (
                <ProposalCard key={p.id} proposal={p} matterId={matterId} onDecided={onProposalDecided} />
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Documents</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-lf-text-muted">
                Upload a PDF or Word document — the assistant extracts details for you to verify
                below. Nothing is filed until you confirm it.
              </p>
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                className="sr-only"
                onChange={onFileChange}
                aria-label="Upload a document for extraction"
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={extracting || done}
                onClick={() => fileInputRef.current?.click()}
              >
                <FileUp className="size-4" aria-hidden="true" />
                {extracting ? 'Extracting…' : 'Upload document'}
              </Button>
              {extractionMsg && (
                <p className="text-xs text-lf-text-muted" role="status">
                  {extractionMsg}
                </p>
              )}
              {extractions.length > 0 && (
                <ul className="space-y-1 text-xs text-lf-text-muted">
                  {extractions.map((e) => (
                    <li key={e.id} className="flex items-center justify-between gap-2">
                      <span className="truncate">{e.filename}</span>
                      <span className="shrink-0">
                        {e.status === 'completed'
                          ? `${e.proposals_created} proposed`
                          : e.status === 'no_readable_text'
                            ? 'no text found'
                            : 'failed'}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Consistency check</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-lf-text-muted">
                Compares the facts you have verified and raises questions for your attorney — never
                legal conclusions.
              </p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={reviewing || done}
                onClick={() => void runReview()}
              >
                <Scale className="size-4" aria-hidden="true" />
                {reviewing ? 'Checking…' : review ? 'Re-run check' : 'Run consistency check'}
              </Button>
              {review && (
                <div className="space-y-2" role="status">
                  {review.findings.length === 0 ? (
                    <p className="text-xs text-lf-text-muted">
                      No questions raised — your verified facts look consistent.
                    </p>
                  ) : (
                    review.findings.map((f: ConsistencyFinding, i: number) => (
                      <div
                        key={`${f.question}-${i}`}
                        className={cn(
                          'rounded-lf-card border p-3',
                          /high|critical|urgent/i.test(f.severity)
                            ? 'border-lf-warning-200 bg-lf-warning-50'
                            : 'border-lf-success-200 bg-lf-success-50',
                        )}
                      >
                        <p
                          className={cn(
                            'text-xs font-medium',
                            /high|critical|urgent/i.test(f.severity)
                              ? 'text-lf-warning-700'
                              : 'text-lf-success-800',
                          )}
                        >
                          {f.question}
                        </p>
                        <p className="mt-1 text-xs text-lf-text-muted">
                          Involves: {f.fields.map(humanizeKey).join(', ')}
                        </p>
                      </div>
                    ))
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
