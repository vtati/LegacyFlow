/**
 * Global save-status bus.
 *
 * Mutating API calls report here (see `trackSave`, wired into the shared API
 * clients); the app-shell header renders the status. The indicator stays hidden
 * until the first real save in the session — it never claims "saved"
 * speculatively.
 *
 * A server-returned 4xx rejection is NOT a failed save: it is an actionable
 * answer (permission denied, invalid, conflict…) that the calling surface
 * presents in context, so `trackSave` clears the indicator for those instead
 * of letting the header cry "Couldn't save — try again" over the real
 * message. Only transient failures — network errors and 5xx responses,
 * where a retry can actually succeed — report `error`.
 */

export type SaveStatus = 'saving' | 'saved' | 'error';

export interface SaveSnapshot {
  status: SaveStatus;
  at: Date;
}

type SaveStatusListener = (snapshot: SaveSnapshot | null) => void;

const listeners = new Set<SaveStatusListener>();
let last: SaveSnapshot | null = null;

export function reportSaveStatus(status: SaveStatus): void {
  last = { status, at: new Date() };
  const snapshot = last;
  listeners.forEach((listener) => listener(snapshot));
}

export function getSaveStatus(): SaveSnapshot | null {
  return last;
}

/** Hide the indicator again — nothing is saving, saved, or failed-to-save. */
export function clearSaveStatus(): void {
  last = null;
  listeners.forEach((listener) => listener(null));
}

/**
 * True when a rejection carries an HTTP 4xx status (the API client's
 * `ApiError` shape, read structurally — this module must not import the API
 * client, which imports `trackSave` from here). Network failures carry no
 * status at all, and 5xx is the server's problem, not the caller's.
 */
function isActionableClientError(err: unknown): boolean {
  if (typeof err !== 'object' || err === null || !('status' in err)) return false;
  const status = (err as { status: unknown }).status;
  return typeof status === 'number' && status >= 400 && status < 500;
}

export function subscribeSaveStatus(listener: SaveStatusListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Wrap a persistence promise so the header save indicator reflects it.
 * Resolves/rejects exactly like the wrapped promise, so existing
 * `.then/.catch/.finally` chains keep working unchanged.
 */
export function trackSave<T>(promise: Promise<T>): Promise<T> {
  reportSaveStatus('saving');
  return promise.then(
    (value) => {
      reportSaveStatus('saved');
      return value;
    },
    (err: unknown) => {
      if (isActionableClientError(err)) {
        // The caller surfaces the server's real message where the user is
        // working; the header must not contradict it with a retry plea.
        clearSaveStatus();
      } else {
        reportSaveStatus('error');
      }
      throw err;
    },
  );
}
