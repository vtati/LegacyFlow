/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { CoverageRing } from '../components/family/coverage-ring';

function stubReducedMotion(matches: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
    onchange: null,
  }));
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('CoverageRing', () => {
  it('renders the final percentage immediately under reduced motion', () => {
    stubReducedMotion(true);
    render(<CoverageRing value={72} label="Sharma plan coverage" />);
    expect(screen.getByText('72%')).toBeDefined();
    const bar = screen.getByRole('progressbar', { name: 'Sharma plan coverage' });
    expect(bar).toHaveAttribute('aria-valuenow', '72');
  });

  it('sets the arc dash offset from the value (reduced motion)', () => {
    stubReducedMotion(true);
    const { container } = render(<CoverageRing value={72} size={152} strokeWidth={12} />);
    const arc = container.querySelector('[data-testid="coverage-ring-arc"]');
    expect(arc).not.toBeNull();
    const circumference = 2 * Math.PI * ((152 - 12) / 2);
    const expected = circumference * (1 - 0.72);
    const actual = Number(arc?.getAttribute('stroke-dashoffset'));
    expect(Math.abs(actual - expected)).toBeLessThan(0.5);
  });

  it('clamps out-of-range values', () => {
    stubReducedMotion(true);
    render(<CoverageRing value={140} />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
    expect(screen.getByText('100%')).toBeDefined();
  });

  it('shows the plan-complete state at 100%', () => {
    stubReducedMotion(true);
    render(<CoverageRing value={100} />);
    expect(screen.getByText('Plan complete')).toBeDefined();
  });

  it('animates to the value when motion is allowed', () => {
    stubReducedMotion(false);
    let rafCallback: FrameRequestCallback | null = null;
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      rafCallback = cb;
      return 1;
    });
    vi.stubGlobal('cancelAnimationFrame', () => {});
    render(<CoverageRing value={72} />);
    expect(rafCallback).not.toBeNull();
    // Drive the animation past its 600ms duration in one deterministic step.
    act(() => {
      (rafCallback as unknown as FrameRequestCallback)(performance.now() + 1000);
    });
    expect(screen.getByText('72%')).toBeDefined();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '72');
  });

  it('renders 0% for a non-finite value instead of NaN', () => {
    stubReducedMotion(true);
    render(<CoverageRing value={Number.NaN} />);
    expect(screen.getByText('0%')).toBeDefined();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0');
  });
});
