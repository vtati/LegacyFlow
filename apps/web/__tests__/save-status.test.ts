/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';
import { ApiError } from '../lib/api-client';
import { getSaveStatus, subscribeSaveStatus, trackSave, type SaveSnapshot } from '../lib/save-status';

/**
 * Stage F / F3: the header save indicator must not cry "Couldn't save — try
 * again" for a server-returned 4xx rejection (e.g. the create-plan 403).
 * Those errors are actionable and are surfaced by the caller in context;
 * the generic retry signal is reserved for transient failures (network
 * errors and 5xx responses), where a retry can actually succeed.
 */
describe('trackSave save-status reporting (F3)', () => {
  it('reports "saved" when the wrapped promise resolves', async () => {
    await expect(trackSave(Promise.resolve(42))).resolves.toBe(42);
    expect(getSaveStatus()?.status).toBe('saved');
  });

  it('reports "saving" while the wrapped promise is pending', async () => {
    let resolvePromise: ((value: string) => void) | undefined;
    const pending = new Promise<string>((resolve) => {
      resolvePromise = resolve;
    });
    const tracked = trackSave(pending);
    expect(getSaveStatus()?.status).toBe('saving');
    resolvePromise?.('done');
    await expect(tracked).resolves.toBe('done');
    expect(getSaveStatus()?.status).toBe('saved');
  });

  it('does NOT report "error" for a 403 permission rejection — the status clears instead', async () => {
    const seen: (SaveSnapshot | null)[] = [];
    const unsubscribe = subscribeSaveStatus((snapshot) => seen.push(snapshot));
    const failure = new ApiError(403, 'Your account does not have permission to create estate plans yet.');
    await expect(trackSave(Promise.reject(failure))).rejects.toBe(failure);
    unsubscribe();
    expect(seen.some((s) => s?.status === 'error')).toBe(false);
    // Nothing truthful for the header to claim: no stuck "Saving…", no
    // "All changes saved", and above all no "Couldn't save — try again".
    expect(getSaveStatus()).toBeNull();
  });

  it('does NOT report "error" for other 4xx business rejections (400/409/422)', async () => {
    for (const status of [400, 409, 422]) {
      const failure = new ApiError(status, 'Rejected by the server.');
      await expect(trackSave(Promise.reject(failure))).rejects.toBe(failure);
      expect(getSaveStatus()).toBeNull();
    }
  });

  it('still reports "error" for a 5xx server failure (a retry can succeed)', async () => {
    const failure = new ApiError(500, 'Internal server error.');
    await expect(trackSave(Promise.reject(failure))).rejects.toBe(failure);
    expect(getSaveStatus()?.status).toBe('error');
  });

  it('still reports "error" for a network failure (no HTTP status at all)', async () => {
    const failure = new TypeError('Failed to fetch');
    await expect(trackSave(Promise.reject(failure))).rejects.toBe(failure);
    expect(getSaveStatus()?.status).toBe('error');
  });
});
