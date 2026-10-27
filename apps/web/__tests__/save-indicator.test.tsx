/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { SaveIndicator } from '../components/save-indicator';
import { ApiError } from '../lib/api-client';
import { trackSave } from '../lib/save-status';

/**
 * Stage F / F3 at the surface the user sees: the header save indicator is
 * the element Stage D photographed as the contradictory "toast". A 4xx
 * rejection must leave it silent (the caller shows the real message);
 * a transient failure must still light it up.
 */
describe('SaveIndicator (F3)', () => {
  it('stays silent on a 403 rejection but still warns on a network failure', async () => {
    render(<SaveIndicator />);
    expect(screen.queryByText(/Couldn’t save/)).toBeNull();

    await act(async () => {
      await expect(
        trackSave(Promise.reject(new ApiError(403, 'Permission denied.'))),
      ).rejects.toThrow('Permission denied.');
    });
    expect(screen.queryByText(/Couldn’t save/)).toBeNull();
    expect(screen.queryByText(/Saving/)).toBeNull();
    expect(screen.queryByText(/All changes saved/)).toBeNull();

    await act(async () => {
      await expect(trackSave(Promise.reject(new TypeError('Failed to fetch')))).rejects.toThrow(
        'Failed to fetch',
      );
    });
    expect(screen.getByText(/Couldn’t save — try again/)).toBeDefined();
  });
});
