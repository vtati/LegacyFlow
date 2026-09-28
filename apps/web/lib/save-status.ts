/**
 * Global save-status bus.
 *
 * Mutating API calls report here (see `trackSave`, wired into the shared API
 * clients); the app-shell header renders the status. The indicator stays hidden
 * until the first real save in the session — it never claims "saved"
 * speculatively.
 */

export type SaveStatus = 'saving' | 'saved' | 'error';

export interface SaveSnapshot {
  status: SaveStatus;
  at: Date;
}

type SaveStatusListener = (snapshot: SaveSnapshot) => void;

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
      reportSaveStatus('error');
      throw err;
    },
  );
}
