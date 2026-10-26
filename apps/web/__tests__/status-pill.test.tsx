/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StatusPill } from '../components/family/status-pill';

describe('StatusPill (family jewel pill)', () => {
  it('renders the formatted status text', () => {
    render(<StatusPill status="in_review" />);
    expect(screen.getByText('In Review')).toBeDefined();
  });

  it('uses the mint jewel for completed or approved statuses', () => {
    const { container } = render(<StatusPill status="completed" />);
    expect(container.firstChild).toHaveClass('bg-lf-success-100');
    expect(container.firstChild).toHaveClass('text-lf-success-700');
  });

  it('uses the sky jewel (information) for review states', () => {
    const { container } = render(<StatusPill status="in_review" />);
    expect(container.firstChild).toHaveClass('bg-lf-sky-100');
    expect(container.firstChild).toHaveClass('text-lf-sky-800');
  });

  it('uses the rose jewel for rejected or blocked statuses', () => {
    const { container } = render(<StatusPill status="rejected" />);
    expect(container.firstChild).toHaveClass('bg-lf-error-100');
    expect(container.firstChild).toHaveClass('text-lf-error-700');
  });

  it('uses the amber jewel for in-progress statuses', () => {
    const { container } = render(<StatusPill status="in_progress" />);
    expect(container.firstChild).toHaveClass('bg-lf-warning-100');
    expect(container.firstChild).toHaveClass('text-lf-warning-700');
  });

  it('falls back to a neutral pill for other statuses', () => {
    const { container } = render(<StatusPill status="draft" />);
    expect(container.firstChild).toHaveClass('bg-lf-canvas-subtle');
    expect(container.firstChild).toHaveClass('text-lf-text-muted');
  });

  it('maps every real MatterStatus enum value to a considered tone', () => {
    const cases: Array<[string, string]> = [
      ['DRAFT', 'bg-lf-canvas-subtle'],
      ['INTAKE_IN_PROGRESS', 'bg-lf-warning-100'],
      ['INTAKE_SUBMITTED', 'bg-lf-sky-100'],
      ['ATTORNEY_REVIEW', 'bg-lf-sky-100'],
      ['CLARIFICATION_REQUESTED', 'bg-lf-warning-100'],
      ['ESCALATED', 'bg-lf-error-100'],
      ['APPROVED_FOR_ASSEMBLY', 'bg-lf-success-100'],
      ['DOCUMENTS_RELEASED', 'bg-lf-sky-100'],
      ['SIGNING_IN_PROGRESS', 'bg-lf-warning-100'],
      ['FUNDING_IN_PROGRESS', 'bg-lf-warning-100'],
      ['PLAN_IMPLEMENTED', 'bg-lf-success-100'],
      ['ARCHIVED', 'bg-lf-canvas-subtle'],
    ];
    for (const [status, expectedClass] of cases) {
      const { container, unmount } = render(<StatusPill status={status} />);
      expect(container.firstChild, status).toHaveClass(expectedClass);
      unmount();
    }
  });
});
