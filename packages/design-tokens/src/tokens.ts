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
    /** Cards (16px). */
    card: string;
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
  shadow: { none: string; subtle: string; card: string };
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
      50: '#ECF9F4',
      100: '#D1F2E3',
      200: '#A5E4CB',
      300: '#6ED0AB',
      400: '#3AB98A',
      500: '#1F9D74',
      600: '#17805E',
      700: '#14664C',
      800: '#12523E',
      900: '#0F4333',
      950: '#07271E',
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
      DEFAULT: '#F7F9FA',
      subtle: '#EEF2F4',
      elevated: '#FFFFFF',
    },
    text: {
      DEFAULT: '#0B1526',
      muted: '#5A6B7D',
      subtle: '#556B80',
      inverse: '#FFFFFF',
    },
    border: {
      DEFAULT: '#DEE6EC',
      strong: '#C3D0DB',
    },
    gold: '#0E9BBF',
  },
  radius: {
    none: '0px',
    sm: '6px',
    control: '10px',
    md: '12px',
    card: '16px',
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
    card: '0 1px 2px rgb(11 21 38 / 0.05), 0 8px 24px -8px rgb(11 21 38 / 0.12)',
  },
};

export type TokenColors = Tokens['colors'];
export type TokenRadius = Tokens['radius'];
export type TokenFont = Tokens['font'];
