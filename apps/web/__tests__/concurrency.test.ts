/**
 * PERF-017: the checkup page fanned out ~8 API calls per matter across
 * ALL matters at once. mapWithConcurrency caps the burst (2 matters at
 * a time) while preserving result order.
 */
import { describe, expect, it } from 'vitest';
import { mapWithConcurrency } from '../lib/concurrency';

describe('mapWithConcurrency (PERF-017)', () => {
  it('never exceeds the limit, preserves order, and runs everything', async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    const items = [0, 1, 2, 3, 4, 5];
    const results = await mapWithConcurrency(items, 2, async (n) => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 5));
      inFlight -= 1;
      return n * 10;
    });
    expect(maxInFlight).toBeLessThanOrEqual(2);
    expect(maxInFlight).toBe(2); // the pool actually parallelizes
    expect(results).toEqual([0, 10, 20, 30, 40, 50]);
  });

  it('handles an empty list', async () => {
    await expect(mapWithConcurrency([], 2, async (n: number) => n)).resolves.toEqual([]);
  });
});
