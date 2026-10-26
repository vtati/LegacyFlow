/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EmptyState } from '../components/family/page-state';

describe('EmptyState', () => {
  it('renders the plain treatment by default (shared with professional pages)', () => {
    const { container } = render(<EmptyState title="Nothing here" description="Come back later." />);
    expect(screen.getByText('Nothing here')).toBeDefined();
    expect(container.querySelector('.bg-lf-aurora')).toBeNull();
  });

  it('renders the aurora treatment when the aurora variant is requested', () => {
    const { container } = render(
      <EmptyState title="No documents yet" description="Drafts will appear here." variant="aurora" />,
    );
    expect(screen.getByText('No documents yet')).toBeDefined();
    expect(container.querySelector('.bg-lf-aurora')).not.toBeNull();
    expect(container.querySelector('.bg-lf-gradient-action')).not.toBeNull();
  });
});
