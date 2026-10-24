import { describe, expect, it } from 'vitest';
import { darkTokens, legacyflowPreset, tokens } from '../index';

describe('design tokens — Atelier Light / Lumen Ink', () => {
  it('exports the token object with the expected scales', () => {
    // Ink-navy primary ramp (900 = brand ink #0B1526)
    expect(tokens.colors.primary[950]).toBe('#060C16');
    expect(tokens.colors.primary[900]).toBe('#0B1526');
    expect(tokens.colors.primary[800]).toBe('#132A42');
    expect(tokens.colors.primary[600]).toBe('#2E4E68');
    expect(tokens.colors.primary.foreground).toBe('#FFFFFF');
    // Success — modern emerald (#1F9D74 at 500)
    expect(tokens.colors.success[700]).toBe('#0A6B51');
    expect(tokens.colors.success[600]).toBe('#0B8663');
    expect(tokens.colors.success[500]).toBe('#0EA47A');
    expect(tokens.colors.success[100]).toBe('#D3F6E6');
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
    expect(tokens.colors.canvas.DEFAULT).toBe('#F4F9FC');
    expect(tokens.colors.canvas.subtle).toBe('#EAF3F8');
    expect(tokens.colors.canvas.elevated).toBe('#FFFFFF');
    // Text — ink-navy family
    expect(tokens.colors.text.DEFAULT).toBe('#0B1526');
    expect(tokens.colors.text.muted).toBe('#5A6B7D');
    expect(tokens.colors.text.subtle).toBe('#556B80');
    expect(tokens.colors.text.inverse).toBe('#FFFFFF');
    // Borders — cool hairlines (ink at low alpha, as hex)
    expect(tokens.colors.border.DEFAULT).toBe('#DFEBF2');
    expect(tokens.colors.border.strong).toBe('#CBE0EC');
    // Gold token repurposed as the Lumen bright (legacy name kept for compatibility)
    expect(tokens.colors.gold).toBe('#0E9BBF');
    // Radius — 16px cards per Direction A (--radius scale 0.75rem family)
    expect(tokens.radius.card).toBe('18px');
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

describe('dark tokens — Noir Desk / Mission Graphite (Direction C)', () => {
  it('exports the dark token overrides with the expected values', () => {
    // Surfaces — C spec: canvas / panel / raised
    expect(darkTokens.colors.canvas.DEFAULT).toBe('#0A0E13');
    expect(darkTokens.colors.canvas.subtle).toBe('#11161D');
    expect(darkTokens.colors.canvas.elevated).toBe('#1A212B');
    // Text — C spec
    expect(darkTokens.colors.text.DEFAULT).toBe('#ECEFF3');
    expect(darkTokens.colors.text.muted).toBe('#98A4B3');
    expect(darkTokens.colors.text.subtle).toBe('#8494A7');
    // Jewel — dispatch cyan, with dark foreground for filled actions
    expect(darkTokens.colors.accent[600]).toBe('#4CC3FF');
    expect(darkTokens.colors.accent[500]).toBe('#4CC3FF');
    expect(darkTokens.colors.accent[100]).toBe('#0C2431');
    expect(darkTokens.colors.accent.foreground).toBe('#06222E');
    // Primary rotates for text steps but 900/950 stay dark surfaces (buttons/bands keep white text)
    expect(darkTokens.colors.primary[800]).toBe('#C4D9EC');
    expect(darkTokens.colors.primary[900]).toBe('#16283E');
    expect(darkTokens.colors.primary[950]).toBe('#0F1D2E');
    expect(darkTokens.colors.primary.foreground).toBe('#FFFFFF');
    // Hairlines — white at low alpha, as hex
    expect(darkTokens.colors.border.DEFAULT).toBe('#272C34');
    expect(darkTokens.colors.border.strong).toBe('#363B42');
    // Alert red from C spec lives in the error ramp
    expect(darkTokens.colors.error[500]).toBe('#FF5C47');
    // Radius + fonts are shared with light (not overridden)
    expect('radius' in darkTokens).toBe(false);
  });

describe('design tokens — Lumen Aurora additions (2026-10-24)', () => {
  it('adds sky and violet ramps with the deck values', () => {
    // Sky — information (400 = #38BDF8 deck sky; 500 = #1899D6 working step)
    expect(tokens.colors.sky[400]).toBe('#38BDF8');
    expect(tokens.colors.sky[500]).toBe('#1899D6');
    expect(tokens.colors.sky[100]).toBe('#E7F5FE');
    expect(tokens.colors.sky[700]).toBe('#0D6594');
    expect(tokens.colors.sky.foreground).toBe('#FFFFFF');
    // Violet — the AI color (500 = #7C6CF0 deck violet; 700 = #5B4BD6 AA text step)
    expect(tokens.colors.violet[500]).toBe('#7C6CF0');
    expect(tokens.colors.violet[700]).toBe('#5B4BD6');
    expect(tokens.colors.violet[100]).toBe('#EFEDFE');
    expect(tokens.colors.violet[400]).toBe('#9D8DF3');
    expect(tokens.colors.violet.foreground).toBe('#FFFFFF');
  });

  it('adds jewel panel tints (deck soft surfaces)', () => {
    expect(tokens.colors.panel.cyan).toBe('#E3F5FA');
    expect(tokens.colors.panel.sky).toBe('#E7F5FE');
    expect(tokens.colors.panel.violet).toBe('#EFEDFE');
    expect(tokens.colors.panel.mint).toBe('#E4F6EF');
    expect(tokens.colors.panel.amber).toBe('#FDF3E0');
    expect(tokens.colors.panel.rose).toBe('#FCEBF1');
  });

  it('adds the signature gradients', () => {
    expect(tokens.gradient.primary).toBe(
      'linear-gradient(115deg, #22D3EE 0%, #0E9BBF 45%, #7C6CF0 100%)',
    );
    expect(tokens.gradient.aurora).toContain('radial-gradient');
    expect(tokens.gradient.aurora).toContain('56 189 248');
    expect(tokens.gradient.aurora).toContain('124 108 240');
    expect(tokens.gradient.aurora).toContain('34 211 238');
    // Action gradient (white-text-safe at every blend point, AA report 2026-10-24)
    expect(tokens.gradient.action).toBe('linear-gradient(115deg, #0E7C9C 0%, #6553E3 100%)');
    expect(tokens.colors.sky[600]).toBe('#0E77AE');
  });

  it('adds hero radius and glow shadow', () => {
    expect(tokens.radius.hero).toBe('20px');
    expect(tokens.shadow.glow).toContain('14 155 191');
  });

  it('maps the new tokens in the tailwind preset', () => {
    const colors = (legacyflowPreset.theme?.extend?.colors ?? {}) as Record<string, unknown>;
    const bg = (legacyflowPreset.theme?.extend?.backgroundImage ?? {}) as Record<string, string>;
    const radius = (legacyflowPreset.theme?.extend?.borderRadius ?? {}) as Record<string, string>;
    const shadows = (legacyflowPreset.theme?.extend?.boxShadow ?? {}) as Record<string, string>;
    const sky = colors['lf-sky'] as Record<string, string>;
    const violet = colors['lf-violet'] as Record<string, string>;
    const panel = colors['lf-panel'] as Record<string, string>;
    expect(sky[500]).toBe('var(--lf-sky-500)');
    expect(violet[700]).toBe('var(--lf-violet-700)');
    expect(panel.violet).toBe('var(--lf-panel-violet)');
    expect(bg['lf-gradient']).toBe('var(--lf-gradient-primary)');
    expect(bg['lf-gradient-action']).toBe('var(--lf-gradient-action)');
    expect(bg['lf-aurora']).toBe('var(--lf-gradient-aurora)');
    expect(radius['lf-hero']).toBe('var(--lf-radius-hero)');
    expect(shadows['lf-glow']).toBe('var(--lf-shadow-glow)');
  });

  it('dark tokens carry sky/violet/panel parity (role-rotated)', () => {
    expect(darkTokens.colors.sky[400]).toBe('#38BDF8');
    expect(darkTokens.colors.violet[400]).toBe('#7C6CF0');
    expect(darkTokens.colors.panel.violet).toBe('#1D1840');
    expect(darkTokens.colors.panel.cyan).toBe('#0C2431');
    expect(darkTokens.gradient.primary).toBe(tokens.gradient.primary);
    expect(darkTokens.shadow.glow).toContain('76 195 255');
  });
});

  it('preset enables class-based dark mode', () => {
    expect(legacyflowPreset.darkMode).toBe('class');
  });
});
