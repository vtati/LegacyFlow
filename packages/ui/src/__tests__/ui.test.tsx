/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Button } from '../button';
import { Card, CardContent, CardHeader, CardTitle } from '../card';

describe('Button', () => {
  it('renders with accessible label and primary styling by default', () => {
    render(<Button>Continue</Button>);
    const btn = screen.getByRole('button', { name: 'Continue' });
    expect(btn).toBeDefined();
    expect(btn.className).toContain('bg-lf-primary-900');
  });

  it('applies the accent variant and small size', () => {
    render(
      <Button variant="accent" size="sm">
        Save
      </Button>,
    );
    const btn = screen.getByRole('button', { name: 'Save' });
    expect(btn.className).toContain('bg-lf-accent-600');
    expect(btn.className).toContain('h-8');
  });

  it('is keyboard-focusable and respects disabled', () => {
    render(<Button disabled>Blocked</Button>);
    const btn = screen.getByRole('button', { name: 'Blocked' }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.className).toContain('focus-visible:ring-2');
  });
});

describe('Card', () => {
  it('renders title and content with card styling', () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>My Plan</CardTitle>
        </CardHeader>
        <CardContent>Placeholder</CardContent>
      </Card>,
    );
    expect(screen.getByText('My Plan')).toBeDefined();
    expect(screen.getByText('Placeholder')).toBeDefined();
  });
});
