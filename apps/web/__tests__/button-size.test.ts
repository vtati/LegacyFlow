/**
 * UI-020: size="sm" buttons were 32px tall everywhere — below the 44px
 * touch target on mobile. The variant is now h-11 on mobile and
 * sm:h-8 on desktop.
 */
import { describe, expect, it } from 'vitest';
import { buttonVariants } from '../components/ui/button';

describe('buttonVariants size="sm" (UI-020)', () => {
  it('is a 44px touch target on mobile and compact on desktop', () => {
    const cls = buttonVariants({ size: 'sm' });
    expect(cls).toContain('h-11');
    expect(cls).toContain('sm:h-8');
  });
});
