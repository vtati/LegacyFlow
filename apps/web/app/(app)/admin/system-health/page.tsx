'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, Clock3, Server } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { LoadingState } from '@/components/family/page-state';
import { FirmScopeNote } from '@/components/admin/admin-page-state';
import { formatDateTime } from '@/lib/format';
import { cn } from '@/lib/utils';

interface HealthzResponse {
  status: string;
  service: string;
  version: string;
  env: string;
}

type HealthState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | {
      kind: 'ok';
      payload: HealthzResponse;
      latencyMs: number;
      checkedAt: string;
      url: string;
    }
  | { kind: 'error'; message: string; checkedAt: string; url: string };

/**
 * The API origin, derived the same way api-client does: ApiClientOptions'
 * baseUrl defaults to '' — same-origin — and no NEXT_PUBLIC_API_URL-style
 * override is configured. /healthz lives at the API origin without the /v1
 * prefix (apiFetch adds /v1 automatically, so health uses the raw origin).
 */
function apiOrigin(): string {
  return window.location.origin;
}

function useApiHealth() {
  const [state, setState] = useState<HealthState>({ kind: 'idle' });
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const check = useCallback(async () => {
    const url = `${apiOrigin()}/healthz`;
    setState({ kind: 'loading' });
    const started = performance.now();
    try {
      const response = await fetch(url, { method: 'GET', cache: 'no-store' });
      const latencyMs = Math.round(performance.now() - started);
      if (!response.ok) {
        throw new Error(`Health check returned HTTP ${response.status}.`);
      }
      const payload = (await response.json()) as HealthzResponse;
      if (mounted.current) {
        setState({
          kind: 'ok',
          payload,
          latencyMs,
          checkedAt: new Date().toISOString(),
          url,
        });
      }
    } catch (err: unknown) {
      if (mounted.current) {
        setState({
          kind: 'error',
          message: err instanceof Error ? err.message : 'The health check failed.',
          checkedAt: new Date().toISOString(),
          url,
        });
      }
    }
  }, []);

  return { state, check };
}

function HealthCard({
  state,
  onRetry,
}: {
  state: HealthState;
  onRetry: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <span className="flex size-10 items-center justify-center rounded-lf-control bg-lf-primary-50 text-lf-primary-800">
            <Server className="size-5" aria-hidden="true" />
          </span>
          <div>
            <CardTitle>API health</CardTitle>
            <CardDescription>Live check against the API origin&apos;s /healthz endpoint.</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {state.kind === 'loading' && <LoadingState lines={3} label="Checking API health…" />}

        {state.kind === 'ok' && (
          <dl className="grid gap-4 sm:grid-cols-2">
            <div className="rounded-lf-control border border-lf-border p-4">
              <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-muted">
                Status
              </dt>
              <dd className="mt-1">
                <span className="inline-block rounded-full bg-lf-success-100 px-2.5 py-1 text-xs font-medium text-lf-success-700">
                  {state.payload.status}
                </span>
              </dd>
            </div>
            <div className="rounded-lf-control border border-lf-border p-4">
              <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-muted">
                Round-trip latency
              </dt>
              <dd className="mt-1 text-sm font-semibold text-lf-text">
                {state.latencyMs} ms
              </dd>
            </div>
            <div className="rounded-lf-control border border-lf-border p-4">
              <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-muted">
                Service
              </dt>
              <dd className="mt-1 font-mono text-xs text-lf-text">
                {state.payload.service} · {state.payload.version} · {state.payload.env}
              </dd>
            </div>
            <div className="rounded-lf-control border border-lf-border p-4">
              <dt className="text-xs font-medium uppercase tracking-wide text-lf-text-muted">
                Checked at
              </dt>
              <dd className="mt-1 text-sm text-lf-text">{formatDateTime(state.checkedAt)}</dd>
            </div>
          </dl>
        )}

        {state.kind === 'error' && (
          <div role="alert" className="space-y-2">
            <p className="text-sm font-medium text-lf-error-700">
              Could not reach the API health endpoint.
            </p>
            <p className="text-sm text-lf-text-muted">{state.message}</p>
            <p className="text-xs text-lf-text-subtle">
              Checked {formatDateTime(state.checkedAt)} · {state.url}
            </p>
          </div>
        )}

        {state.kind !== 'loading' && (
          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" variant="outline" className="min-h-[44px]" onClick={onRetry}>
              {state.kind === 'idle' ? 'Check now' : 'Re-check'}
            </Button>
            {state.kind !== 'idle' && (
              <p className="font-mono text-xs text-lf-text-subtle">{state.url}</p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Background services we can name but cannot verify from the browser: the
 * reminder cron and the worker run on Railway on a schedule. No endpoint
 * exposes their live status, so this section says exactly that.
 */
function BackgroundServicesCard() {
  const services = [
    {
      name: 'Reminder cron',
      description:
        'Scheduled every 15 minutes. Sends plan reminders and other time-based nudges.',
      cadence: 'Scheduled',
    },
    {
      name: 'Background worker',
      description:
        'Dramatiq worker that processes async jobs (document assembly, notifications).',
      cadence: 'Scheduled',
    },
  ];
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <span className="flex size-10 items-center justify-center rounded-lf-control bg-lf-primary-50 text-lf-primary-800">
            <Clock3 className="size-5" aria-hidden="true" />
          </span>
          <div>
            <CardTitle>Background services</CardTitle>
            <CardDescription>
              Jobs that run outside the API request lifecycle.
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <ul className="space-y-3">
          {services.map((service) => (
            <li
              key={service.name}
              className="flex flex-wrap items-start justify-between gap-3 rounded-lf-control border border-lf-border p-4"
            >
              <div className="space-y-1">
                <p className="flex items-center gap-2 text-sm font-semibold text-lf-text">
                  <Activity className="size-4 text-lf-text-subtle" aria-hidden="true" />
                  {service.name}
                </p>
                <p className="text-sm text-lf-text-muted">{service.description}</p>
                <p className="text-xs text-lf-text-subtle">
                  Runs on Railway infrastructure, managed outside this web app.
                </p>
              </div>
              <div className="text-right">
                <span
                  className={cn(
                    'inline-block rounded-full bg-lf-canvas-subtle px-2.5 py-1 text-xs font-medium text-lf-text-muted',
                  )}
                >
                  {service.cadence}
                </span>
                <p className="mt-1 text-xs text-lf-text-subtle">Status not exposed by the API</p>
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

/** Admin → System Health: API health check plus honest background-service status. */
export default function AdminSystemHealthPage(): React.JSX.Element {
  const { state, check } = useApiHealth();

  useEffect(() => {
    check();
  }, [check]);

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">System Health</h1>
        <p className="mt-2 text-sm text-lf-text-muted">
          Live service status for the API your firm depends on.
        </p>
      </div>

      <FirmScopeNote>
        Health checks describe the shared API service; the background-services section
        only claims what the API exposes — nothing invented.
      </FirmScopeNote>

      <HealthCard state={state} onRetry={check} />
      <BackgroundServicesCard />
    </div>
  );
}
