import { describe, expect, it } from 'vitest';
import { legacyflowPreset, tokens } from '../index';

describe('design tokens', () => {
  it('exports the token object with the expected scales', () => {
    expect(tokens.colors.primary[950]).toBe('#12100D');
    expect(tokens.colors.primary[800]).toBe('#26221B');
    expect(tokens.colors.primary[600]).toBe('#4A4234');
    expect(tokens.colors.primary.foreground).toBe('#FAF7F1');
    expect(tokens.colors.success[700]).toBe('#3D6659');
    expect(tokens.colors.success[600]).toBe('#4D7C6B');
    expect(tokens.colors.success[100]).toBe('#E4EFE9');
    expect(tokens.colors.accent[700]).toBe('#8A3E26');
    expect(tokens.colors.accent[500]).toBe('#C15F3C');
    expect(tokens.colors.accent[100]).toBe('#F7E3D7');
    expect(tokens.colors.error[700]).toBe('#8A3210');
    expect(tokens.colors.error[600]).toBe('#A93E12');
    expect(tokens.colors.error[100]).toBe('#F8D9CC');
    expect(tokens.colors.warning[500]).toBe('#D97706');
    expect(tokens.colors.warning[600]).toBe('#B45309');
    expect(tokens.colors.warning[100]).toBe('#FEF3C7');
    expect(tokens.colors.warning[50]).toBe('#FFFBEB');
    expect(tokens.colors.warning[700]).toBe('#92400E');
    expect(tokens.colors.warning.foreground).toBe('#FFFFFF');
    expect(tokens.colors.canvas.DEFAULT).toBe('#FAF7F1');
    expect(tokens.colors.canvas.subtle).toBe('#F3EEE3');
    expect(tokens.colors.canvas.elevated).toBe('#FFFFFF');
    expect(tokens.colors.text.DEFAULT).toBe('#1C1917');
    expect(tokens.colors.text.muted).toBe('#57534E');
    expect(tokens.colors.text.subtle).toBe('#78716C');
    expect(tokens.colors.text.inverse).toBe('#FAF7F1');
    expect(tokens.colors.border.DEFAULT).toBe('#E8E2D5');
    expect(tokens.colors.border.strong).toBe('#D9D1BE');
    expect(tokens.colors.gold).toBe('#A98A3D');
    expect(tokens.radius.card).toBe('14px');
    expect(tokens.radius.control).toBe('10px');
    expect(tokens.font.sans[0]).toBe('var(--font-inter)');
    expect(tokens.font.serif[0]).toBe('var(--font-fraunces)');
  });

  it('exports a tailwind preset that maps theme keys to CSS variables', () => {
    const colors = (legacyflowPreset.theme?.extend?.colors ?? {}) as Record<string, unknown>;
    const radius = (legacyflowPreset.theme?.extend?.borderRadius ?? {}) as Record<string, string>;
    const fonts = (legacyflowPreset.theme?.extend?.fontFamily ?? {}) as Record<string, string>;
    expect(colors['lf-primary']).toBeDefined();
    expect(colors['lf-accent']).toBeDefined();
    expect(colors['lf-warning']).toBeDefined();
    expect(colors['lf-gold']).toBeDefined();
    const warning = colors['lf-warning'] as Record<string, string>;
    expect(warning[500]).toBe('var(--lf-warning-500)');
    expect(warning[700]).toBe('var(--lf-warning-700)');
    expect(warning.foreground).toBe('var(--lf-warning-foreground)');
    expect(colors['lf-gold']).toBe('var(--lf-gold)');
    expect(radius['lf-card']).toBe('var(--lf-radius-card)');
    expect(radius['lf-control']).toBe('var(--lf-radius-control)');
    expect(fonts['lf-sans']).toBe('var(--lf-font-sans)');
    expect(fonts['lf-serif']).toBe('var(--lf-font-serif)');
  });
});
