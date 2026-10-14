import { describe, expect, it } from 'vitest';
import { legacyflowPreset, tokens } from '../index';

describe('design tokens — Atelier Light / Lumen Ink', () => {
  it('exports the token object with the expected scales', () => {
    // Ink-navy primary ramp (900 = brand ink #0B1526)
    expect(tokens.colors.primary[950]).toBe('#060C16');
    expect(tokens.colors.primary[900]).toBe('#0B1526');
    expect(tokens.colors.primary[800]).toBe('#132A42');
    expect(tokens.colors.primary[600]).toBe('#2E4E68');
    expect(tokens.colors.primary.foreground).toBe('#FFFFFF');
    // Success — modern emerald (#1F9D74 at 500)
    expect(tokens.colors.success[700]).toBe('#14664C');
    expect(tokens.colors.success[600]).toBe('#17805E');
    expect(tokens.colors.success[500]).toBe('#1F9D74');
    expect(tokens.colors.success[100]).toBe('#D1F2E3');
    // Accent — Lumen cyan (600 = primary action #0E7C9C; 400 = bright #22D3EE, glow/progress only)
    expect(tokens.colors.accent[600]).toBe('#0E7C9C');
    expect(tokens.colors.accent[500]).toBe('#0E9BBF');
    expect(tokens.colors.accent[400]).toBe('#22D3EE');
    expect(tokens.colors.accent[100]).toBe('#D7F3FA');
    // Error — modern red
    expect(tokens.colors.error[700]).toBe('#B91C1C');
    expect(tokens.colors.error[600]).toBe('#DC2626');
    expect(tokens.colors.error[100]).toBe('#FEE2E2');
    // Warning — modern amber
    expect(tokens.colors.warning[500]).toBe('#D97706');
    expect(tokens.colors.warning[600]).toBe('#B45309');
    expect(tokens.colors.warning[100]).toBe('#FEF3C7');
    expect(tokens.colors.warning[50]).toBe('#FFFBEB');
    expect(tokens.colors.warning[700]).toBe('#92400E');
    expect(tokens.colors.warning.foreground).toBe('#FFFFFF');
    // Canvas — cool porcelain (NOT warm ivory)
    expect(tokens.colors.canvas.DEFAULT).toBe('#F7F9FA');
    expect(tokens.colors.canvas.subtle).toBe('#EEF2F4');
    expect(tokens.colors.canvas.elevated).toBe('#FFFFFF');
    // Text — ink-navy family
    expect(tokens.colors.text.DEFAULT).toBe('#0B1526');
    expect(tokens.colors.text.muted).toBe('#5A6B7D');
    expect(tokens.colors.text.subtle).toBe('#556B80');
    expect(tokens.colors.text.inverse).toBe('#FFFFFF');
    // Borders — cool hairlines (ink at low alpha, as hex)
    expect(tokens.colors.border.DEFAULT).toBe('#DEE6EC');
    expect(tokens.colors.border.strong).toBe('#C3D0DB');
    // Gold token repurposed as the Lumen bright (legacy name kept for compatibility)
    expect(tokens.colors.gold).toBe('#0E9BBF');
    // Radius — 16px cards per Direction A (--radius scale 0.75rem family)
    expect(tokens.radius.card).toBe('16px');
    expect(tokens.radius.control).toBe('10px');
    // Type — Geist system; the display ("serif") slot also resolves to Geist
    expect(tokens.font.sans[0]).toBe('var(--font-geist)');
    expect(tokens.font.serif[0]).toBe('var(--font-geist)');
    expect(tokens.font.mono[0]).toBe('var(--font-geist-mono)');
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
