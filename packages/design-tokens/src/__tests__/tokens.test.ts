import { describe, expect, it } from 'vitest';
import { legacyflowPreset, tokens } from '../index';

describe('design tokens', () => {
  it('exports the token object with the expected scales', () => {
    expect(tokens.colors.primary[950]).toBe('#172554');
    expect(tokens.colors.primary[800]).toBe('#1E3A8A');
    expect(tokens.colors.success[700]).toBe('#0F766E');
    expect(tokens.colors.success[600]).toBe('#2D7A6B');
    expect(tokens.colors.accent[700]).toBe('#B45309');
    expect(tokens.colors.accent[800]).toBe('#C2410C');
    expect(tokens.colors.canvas.DEFAULT).toBe('#FAFAF7');
    expect(tokens.radius.card).toBe('14px');
    expect(tokens.radius.control).toBe('8px');
    expect(tokens.font.sans[0]).toBe('Geist');
  });

  it('exports a tailwind preset that maps theme keys to CSS variables', () => {
    const colors = (legacyflowPreset.theme?.extend?.colors ?? {}) as Record<string, unknown>;
    const radius = (legacyflowPreset.theme?.extend?.borderRadius ?? {}) as Record<string, string>;
    const fonts = (legacyflowPreset.theme?.extend?.fontFamily ?? {}) as Record<string, string>;
    expect(colors['lf-primary']).toBeDefined();
    expect(colors['lf-accent']).toBeDefined();
    expect(radius['lf-card']).toBe('var(--lf-radius-card)');
    expect(radius['lf-control']).toBe('var(--lf-radius-control)');
    expect(fonts['lf-sans']).toBe('var(--lf-font-sans)');
  });
});
