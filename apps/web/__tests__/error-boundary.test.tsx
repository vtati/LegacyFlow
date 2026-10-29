/** @vitest-environment jsdom */
/**
 * ERR-008: the web app had no error boundaries — render failures landed
 * on Next's default screen with no recovery path. The segment boundary
 * must offer a plain-language message, a working "Try again" (reset), a
 * link home, reassurance that saved answers persist, and the error
 * digest as the support reference.
 */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import AppError from '../app/error';

describe('app/error.tsx (ERR-008)', () => {
  it('renders branded recovery UI with reset, home link, reassurance, and digest', () => {
    const reset = vi.fn();
    const error = Object.assign(new Error('boom'), { digest: 'digest-42' });
    render(<AppError error={error} reset={reset} />);

    expect(screen.getByText(/something went wrong/i)).toBeDefined();
    expect(screen.getByText(/saved answers/i)).toBeDefined();
    expect(screen.getByText(/digest-42/)).toBeDefined();

    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    expect(reset).toHaveBeenCalledTimes(1);

    expect(screen.getByRole('link', { name: /home/i })).toHaveAttribute('href', '/');
  });

  it('omits the reference line when no digest is provided', () => {
    render(<AppError error={new Error('boom')} reset={vi.fn()} />);
    expect(screen.queryByText(/Reference:/)).toBeNull();
  });
});
