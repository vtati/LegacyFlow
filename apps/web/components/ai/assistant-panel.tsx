'use client';

import { useEffect, useRef, useState } from 'react';
import { Bot, Send } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { ApiError, type AiAskResponse, type AiCitation } from '@/lib/api-client';
import { cn } from '@/lib/utils';

export const AI_DISCLAIMER = 'Educational information only, not legal advice.';

export const AI_REFUSAL_PROMPT =
  'That question needs your attorney’s judgment, not an intake assistant’s.';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  response?: AiAskResponse;
}

interface AssistantPanelProps {
  /** Wired by the caller: family uses `api.askAi`, firm uses `firmApi.askAi`. */
  ask: (question: string) => Promise<AiAskResponse>;
  panelId?: string;
}

function CitationChips({ citations }: { citations: AiCitation[] }) {
  if (citations.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5 pt-1" aria-label="Citations">
      {citations.map((citation, index) => (
        <span
          key={index}
          className="inline-block rounded-full bg-lf-primary-50 px-2.5 py-1 text-xs font-medium text-lf-primary-800"
        >
          {citation.pack} · {citation.section}
        </span>
      ))}
    </div>
  );
}

function AssistantMessage({ message }: { message: ChatMessage }) {
  const response = message.response;
  const refused = response?.refused === true;
  return (
    <div className="flex gap-3">
      <span className="flex size-8 shrink-0 items-center justify-center rounded-lf-control bg-lf-primary-50 text-lf-primary-800">
        <Bot className="size-4" aria-hidden="true" />
      </span>
      <div
        className={cn(
          'min-w-0 flex-1 space-y-2 rounded-lf-card border p-4 text-sm',
          refused ? 'border-lf-accent-600 bg-lf-accent-50' : 'border-lf-border bg-lf-canvas-subtle',
        )}
      >
        {refused ? (
          <>
            <p className="text-lf-text">{response?.safe_message ?? AI_REFUSAL_PROMPT}</p>
            <p className="font-medium text-lf-text">
              Ask your attorney — they can give you advice for your situation.
            </p>
          </>
        ) : (
          <p className="whitespace-pre-wrap text-lf-text">{message.text}</p>
        )}
        {response && <CitationChips citations={response.citations} />}
      </div>
    </div>
  );
}

/**
 * Chat-like assistant panel. Asks POST /v1/matters/{id}/ai/ask via the `ask`
 * prop, renders answers with pack/section citations, and renders refused
 * questions with the safe message plus the "ask your attorney" prompt.
 */
export function AssistantPanel({ ask, panelId = 'ai-assistant' }: AssistantPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ block: 'nearest' });
  }, [messages, asking]);

  const handleAsk = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || asking) return;
    setAsking(true);
    setError(null);
    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      text: trimmed,
    };
    setMessages((prev) => [...prev, userMessage]);
    setQuestion('');
    try {
      const response = await ask(trimmed);
      setMessages((prev) => [
        ...prev,
        {
          id: `assistant-${Date.now()}`,
          role: 'assistant',
          text: response.answer,
          response,
        },
      ]);
    } catch (err: unknown) {
      const denied = err instanceof ApiError && err.isPermissionDenied;
      setError(
        denied
          ? 'Your firm has not granted access to the assistant yet.'
          : err instanceof Error
            ? err.message
            : 'Could not get an answer. Please try again.',
      );
    } finally {
      setAsking(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>AI assistant</CardTitle>
        <CardDescription>
          Ask about your plan in plain language. Answers cite the jurisdiction pack they come from.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div
          role="log"
          aria-label="Assistant conversation"
          aria-live="polite"
          className="max-h-96 space-y-4 overflow-y-auto pr-1"
        >
          {messages.length === 0 && !asking && (
            <p className="rounded-lf-control bg-lf-canvas-subtle p-4 text-sm text-lf-text-muted">
              Try “What does probate mean for my house?” — the assistant answers from your
              jurisdiction’s pack, or points you to your attorney when it can’t.
            </p>
          )}
          {messages.map((message) =>
            message.role === 'user' ? (
              <div key={message.id} className="flex justify-end">
                <p className="max-w-[85%] rounded-lf-card bg-lf-primary-800 px-4 py-2.5 text-sm text-lf-primary-foreground">
                  {message.text}
                </p>
              </div>
            ) : (
              <AssistantMessage key={message.id} message={message} />
            ),
          )}
          {asking && (
            <div className="flex gap-3" role="status" aria-label="Waiting for answer">
              <span className="flex size-8 items-center justify-center rounded-lf-control bg-lf-primary-50 text-lf-primary-800">
                <Bot className="size-4 animate-pulse" aria-hidden="true" />
              </span>
              <p className="rounded-lf-card border border-lf-border bg-lf-canvas-subtle px-4 py-2.5 text-sm text-lf-text-muted">
                Thinking…
              </p>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {error && (
          <p role="alert" className="text-sm text-lf-error-700">
            {error}
          </p>
        )}

        <form onSubmit={handleAsk} className="flex flex-col gap-2">
          <Label htmlFor={`${panelId}-question`} className="sr-only">
            Ask a question
          </Label>
          <div className="flex gap-2">
            <input
              id={`${panelId}-question`}
              type="text"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Ask about your plan…"
              disabled={asking}
              className="h-10 flex-1 rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-3 py-2 text-sm text-lf-text shadow-lf-subtle placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 disabled:opacity-60"
            />
            <Button type="submit" disabled={asking || question.trim().length === 0}>
              <Send className="size-4" aria-hidden="true" />
              <span className="sr-only">Send question</span>
            </Button>
          </div>
        </form>

        <p className="text-xs text-lf-text-subtle">{AI_DISCLAIMER}</p>
      </CardContent>
    </Card>
  );
}
