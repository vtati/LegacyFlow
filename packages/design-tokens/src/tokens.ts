/**
 * LegacyFlow design tokens — TypeScript source of truth.
 *
 * Atelier Light / Lumen Ink palette (selected 2026-10-14, replacing Heritage
 * Editorial): cool porcelain canvas, ink-navy ink, Lumen cyan accent used as
 * jewelry (deep #0E7C9C for actions; bright #22D3EE for progress/glow only),
 * modern emerald success, red error, amber warning.
 * Geist (display + UI) + Geist Mono (indices, kickers, numerals).
 * The same values are published as CSS variables in `tokens.css` and mapped to
 * Tailwind utilities in `tailwind-preset.ts`.
 */

export interface ColorScale {
  50: string;
  100: string;
  200: string;
  300: string;
  400: string;
  500: string;
  600: string;
  700: string;
  800: string;
  900: string;
  950: string;
}

/** Narrower 50–700 scale used by the warning palette. */
export interface WarningScale {
  50: string;
  100: string;
  200: string;
  300: string;
  400: string;
  500: string;
  600: string;
  700: string;
}

export interface Tokens {
  colors: {
    /** Primary brand: ink-navy ramp — 900 is the brand ink, used as text/sidebar ink, never as wallpaper. */
    primary: ColorScale & { foreground: string };
    /** Success/support: modern emerald family. */
    success: ColorScale & { foreground: string };
    /** Action accent: Lumen cyan family — the ONE signature accent, spent sparingly. */
    accent: ColorScale & { foreground: string };
    /** Sky — information (Lumen Aurora, 2026-10-24). */
    sky: ColorScale & { foreground: string };
    /** Violet — the AI color: interview, insights, reviewer (Lumen Aurora, 2026-10-24). */
    violet: ColorScale & { foreground: string };
    /** Error: modern red — never the only communication channel. */
    error: ColorScale & { foreground: string };
    /** Warning: modern amber family (50–700). */
    warning: WarningScale & { foreground: string };
    /** Canvas: cool porcelain surfaces. */
    canvas: { DEFAULT: string; subtle: string; elevated: string };
    /** Text: ink-navy family. */
    text: { DEFAULT: string; muted: string; subtle: string; inverse: string };
    /** Borders: cool hairlines (ink at low alpha). */
    border: { DEFAULT: string; strong: string };
    /** Jewel panel tints — soft colored surfaces (deck softs; Lumen Aurora). */
    panel: { cyan: string; sky: string; violet: string; mint: string; amber: string; rose: string };
    /**
     * Legacy token name kept for compatibility: now the Lumen cyan (accent-500 depth, visible on light surfaces),
     * reserved for progress/glow flourishes + "plan complete" moments only.
     */
    gold: string;
  };
  /** 16px cards; 10px controls. */
  radius: {
    none: string;
    sm: string;
    /** Controls (10px). */
    control: string;
    md: string;
    /** Cards (18px — Lumen Aurora softening, was 16px). */
    card: string;
    /** Hero panels (20px). */
    hero: string;
    lg: string;
    full: string;
  };
  font: {
    /** Geist via next/font; system fallbacks keep rendering deterministic. */
    sans: string[];
    /** Display slot — Geist (Fraunces retired 2026-10-14); kept under the legacy `serif` key so existing `font-lf-serif` utilities render the modern display face. */
    serif: string[];
    mono: string[];
    size: {
      xs: string;
      sm: string;
      base: string;
      lg: string;
      xl: string;
      '2xl': string;
      '3xl': string;
    };
    weight: { regular: number; medium: number; semibold: number; bold: number };
  };
  /** Cool hairlines do the work; tinted two-layer soft shadows for elevation. */
  shadow: { none: string; subtle: string; card: string; glow: string };
  /** Signature gradients (Lumen Aurora): the 115° jewel gradient + the aurora field. */
  gradient: { primary: string; aurora: string; action: string };
}

export const tokens: Tokens = {
  colors: {
    primary: {
      50: '#F2F6F9',
      100: '#E3EBF1',
      200: '#C7D5E0',
      300: '#9FB4C7',
      400: '#6E8AA3',
      500: '#476A87',
      600: '#2E4E68',
      700: '#1E3A52',
      800: '#132A42',
      900: '#0B1526',
      950: '#060C16',
      foreground: '#FFFFFF',
    },
    success: {
      50: '#EDFBF5',
      100: '#D3F6E6',
      200: '#A9EDCF',
      300: '#6EDCB2',
      400: '#38C492',
      500: '#0EA47A',
      600: '#0B8663',
      700: '#0A6B51',
      800: '#095642',
      900: '#084736',
      950: '#052A20',
      foreground: '#FFFFFF',
    },
    accent: {
      50: '#EEFAFD',
      100: '#D7F3FA',
      200: '#B0E7F5',
      300: '#7BD8F0',
      400: '#22D3EE',
      500: '#0E9BBF',
      600: '#0E7C9C',
      700: '#0C6883',
      800: '#0E546B',
      900: '#0F475A',
      950: '#082F3D',
      foreground: '#FFFFFF',
    },
    sky: {
      50: '#EFF8FE',
      100: '#E7F5FE',
      200: '#C9EAFD',
      300: '#93D9FB',
      400: '#38BDF8',
      500: '#1899D6',
      600: '#0E77AE',
      700: '#0D6594',
      800: '#0E547A',
      900: '#0F4564',
      950: '#0A2E44',
      foreground: '#FFFFFF',
    },
    violet: {
      50: '#F3F1FE',
      100: '#EFEDFE',
      200: '#DCD6FC',
      300: '#BDB0F8',
      400: '#9D8DF3',
      500: '#7C6CF0',
      600: '#6553E3',
      700: '#5B4BD6',
      800: '#4A3CAE',
      900: '#3D3289',
      950: '#241D54',
      foreground: '#FFFFFF',
    },
    error: {
      50: '#FEF2F2',
      100: '#FEE2E2',
      200: '#FECACA',
      300: '#FCA5A5',
      400: '#F87171',
      500: '#EF4444',
      600: '#DC2626',
      700: '#B91C1C',
      800: '#991B1B',
      900: '#7F1D1D',
      950: '#450A0A',
      foreground: '#FFFFFF',
    },
    warning: {
      50: '#FFFBEB',
      100: '#FEF3C7',
      200: '#FDE68A',
      300: '#FCD34D',
      400: '#FBBF24',
      500: '#D97706',
      600: '#B45309',
      700: '#92400E',
      foreground: '#FFFFFF',
    },
    canvas: {
      DEFAULT: '#F4F9FC',
      subtle: '#EAF3F8',
      elevated: '#FFFFFF',
    },
    text: {
      DEFAULT: '#0B1526',
      muted: '#5A6B7D',
      subtle: '#556B80',
      inverse: '#FFFFFF',
    },
    border: {
      DEFAULT: '#DFEBF2',
      strong: '#CBE0EC',
    },
    panel: {
      cyan: '#E3F5FA',
      sky: '#E7F5FE',
      violet: '#EFEDFE',
      mint: '#E4F6EF',
      amber: '#FDF3E0',
      rose: '#FCEBF1',
    },
    gold: '#0E9BBF',
  },
  radius: {
    none: '0px',
    sm: '6px',
    control: '10px',
    md: '12px',
    card: '18px',
    hero: '20px',
    lg: '24px',
    full: '9999px',
  },
  font: {
    sans: ['var(--font-geist)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
    serif: ['var(--font-geist)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
    mono: ['var(--font-geist-mono)', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
    size: {
      xs: '0.75rem',
      sm: '0.875rem',
      base: '1rem',
      lg: '1.125rem',
      xl: '1.25rem',
      '2xl': '1.5rem',
      '3xl': '1.875rem',
    },
    weight: { regular: 400, medium: 500, semibold: 600, bold: 700 },
  },
  shadow: {
    none: 'none',
    subtle: '0 1px 2px 0 rgb(11 21 38 / 0.05)',
    card: '0 1px 2px rgb(11 60 90 / 0.06), 0 10px 30px -10px rgb(11 60 90 / 0.14)',
    glow: '0 10px 30px -6px rgb(14 155 191 / 0.45)',
  },
  gradient: {
    primary: 'linear-gradient(115deg, #22D3EE 0%, #0E9BBF 45%, #7C6CF0 100%)',
    aurora:
      'radial-gradient(720px 380px at 108% -10%, rgb(56 189 248 / 0.16), transparent 60%), ' +
      'radial-gradient(640px 360px at -12% 112%, rgb(124 108 240 / 0.12), transparent 60%), ' +
      'radial-gradient(520px 300px at 88% 118%, rgb(34 211 238 / 0.12), transparent 60%)',
    // Deep-segment gradient for elements carrying white text (buttons):
    // every blend point passes 4.5:1 with white — the bright signature
    // gradient is reserved for graphic fills (logo, rings, progress).
    action: 'linear-gradient(115deg, #0E7C9C 0%, #6553E3 100%)',
  },
};

export type TokenColors = Tokens['colors'];
export type TokenRadius = Tokens['radius'];
export type TokenFont = Tokens['font'];

/**
 * Dark-mode overrides — "Noir Desk / Mission Graphite" (Direction C, 2026-10-14).
 *
 * Applied via the `.dark` class on <html> (see `tokens.css`). Only colors and
 * shadows are overridden; radius and fonts are shared with light.
 * Ramps are ROLE-rotated, not inverted: tint steps (50–200) become deep
 * surfaces, text steps (600–800) become light, and primary 900/950 stay dark
 * surfaces because components pair them with white text (buttons, bands).
 */
export interface DarkTokens {
  colors: Tokens['colors'];
  shadow: Tokens['shadow'];
  gradient: Tokens['gradient'];
}

export const darkTokens: DarkTokens = {
  colors: {
    primary: {
      50: '#131E2C',
      100: '#182635',
      200: '#1F3049',
      300: '#2C4562',
      400: '#41648C',
      500: '#5B82AB',
      600: '#84A7C9',
      700: '#ADC6DE',
      800: '#C4D9EC',
      900: '#16283E',
      950: '#0F1D2E',
      foreground: '#FFFFFF',
    },
    success: {
      50: '#0A1F18',
      100: '#0D2B20',
      200: '#123A2C',
      300: '#1B5440',
      400: '#2E8F6B',
      500: '#3AB98A',
      600: '#34C98E',
      700: '#5ED3A5',
      800: '#93E4C3',
      900: '#C9F2E0',
      950: '#ECF9F4',
      foreground: '#052E1F',
    },
    accent: {
      50: '#081A24',
      100: '#0C2431',
      200: '#10303F',
      300: '#17506B',
      400: '#22D3EE',
      500: '#4CC3FF',
      600: '#4CC3FF',
      700: '#7BD8F0',
      800: '#A5E7F5',
      900: '#D7F3FA',
      950: '#EEFAFD',
      foreground: '#06222E',
    },
    sky: {
      50: '#0A1E2C',
      100: '#0C2739',
      200: '#10334A',
      300: '#17506B',
      400: '#38BDF8',
      500: '#38BDF8',
      600: '#7BD0FB',
      700: '#A5E0FC',
      800: '#C9EAFD',
      900: '#E7F5FE',
      950: '#EFF8FE',
      foreground: '#06222E',
    },
    violet: {
      50: '#171233',
      100: '#1D1840',
      200: '#271F52',
      300: '#372B73',
      400: '#7C6CF0',
      500: '#9D8DF3',
      600: '#BDB0F8',
      700: '#DCD6FC',
      800: '#E4E0FD',
      900: '#EFEDFE',
      950: '#F3F1FE',
      foreground: '#1B1440',
    },
    error: {
      50: '#2A1210',
      100: '#381714',
      200: '#4A1D18',
      300: '#6E2A20',
      400: '#C24A38',
      500: '#FF5C47',
      600: '#F87171',
      700: '#FCA5A5',
      800: '#FECACA',
      900: '#FEE2E2',
      950: '#FEF2F2',
      foreground: '#2A0A06',
    },
    warning: {
      50: '#261A05',
      100: '#33230A',
      200: '#45300E',
      300: '#6B4A12',
      400: '#A87416',
      500: '#F59E0B',
      600: '#FBBF24',
      700: '#FCD34D',
      foreground: '#2A1A02',
    },
    canvas: {
      DEFAULT: '#0A0E13',
      subtle: '#11161D',
      elevated: '#1A212B',
    },
    text: {
      DEFAULT: '#ECEFF3',
      muted: '#98A4B3',
      subtle: '#8494A7',
      inverse: '#0B1526',
    },
    border: {
      DEFAULT: '#272C34',
      strong: '#363B42',
    },
    panel: {
      cyan: '#0C2431',
      sky: '#0C2739',
      violet: '#1D1840',
      mint: '#0D2B20',
      amber: '#33230A',
      rose: '#381714',
    },
    gold: '#4CC3FF',
  },
  shadow: {
    none: 'none',
    subtle: '0 1px 2px 0 rgb(0 0 0 / 0.4)',
    card: '0 1px 2px rgb(0 0 0 / 0.4), 0 8px 24px -8px rgb(0 0 0 / 0.5)',
    glow: '0 10px 30px -6px rgb(76 195 255 / 0.35)',
  },
  gradient: {
    primary: 'linear-gradient(115deg, #22D3EE 0%, #0E9BBF 45%, #7C6CF0 100%)',
    aurora:
      'radial-gradient(720px 380px at 108% -10%, rgb(56 189 248 / 0.22), transparent 60%), ' +
      'radial-gradient(640px 360px at -12% 112%, rgb(124 108 240 / 0.18), transparent 60%), ' +
      'radial-gradient(520px 300px at 88% 118%, rgb(34 211 238 / 0.18), transparent 60%)',
    action: 'linear-gradient(115deg, #0E7C9C 0%, #6553E3 100%)',
  },
};
