/**
 * LegacyFlow design tokens — TypeScript source of truth.
 *
 * Modern Fintech palette: clean slate canvas, indigo primary, violet accent,
 * emerald success, standard red error, amber warning. Inter only — no serif.
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
    /** Primary brand: indigo ramp. */
    primary: ColorScale & { foreground: string };
    /** Success/support: emerald family. */
    success: ColorScale & { foreground: string };
    /** Action accent: violet family (pairs with indigo for gradients). */
    accent: ColorScale & { foreground: string };
    /** Error: red — never the only communication channel. */
    error: ColorScale & { foreground: string };
    /** Warning: amber family (50–700). */
    warning: WarningScale & { foreground: string };
    /** Canvas: slate surfaces. */
    canvas: { DEFAULT: string; subtle: string; elevated: string };
    /** Text: slate ink. */
    text: { DEFAULT: string; muted: string; subtle: string; inverse: string };
    /** Borders: slate hairlines. */
    border: { DEFAULT: string; strong: string };
    /** Gold: deprecated — maps to indigo-300 for backward compat. */
    gold: string;
  };
  /** 12–16px cards; 8–10px controls. */
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
    /** Inter via next/font (§5.2); system fallbacks keep rendering deterministic. */
    sans: string[];
    /** Deprecated — maps to Inter. No serif in the Fintech theme. */
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
  /** Soft layered shadows for the fintech floating-card look. */
  shadow: { none: string; subtle: string; card: string };
}

export const tokens: Tokens = {
  colors: {
    primary: {
      50: '#EEF2FF',
      100: '#E0E7FF',
      200: '#C7D2FE',
      300: '#A5B4FC',
      400: '#818CF8',
      500: '#6366F1',
      600: '#4F46E5',
      700: '#4338CA',
      800: '#3730A3',
      900: '#312E81',
      950: '#1E1B4B',
      foreground: '#FFFFFF',
    },
    success: {
      50: '#ECFDF5',
      100: '#D1FAE5',
      200: '#A7F3D0',
      300: '#6EE7B7',
      400: '#34D399',
      500: '#10B981',
      600: '#059669',
      700: '#047857',
      800: '#065F46',
      900: '#064E3B',
      950: '#022C22',
      foreground: '#FFFFFF',
    },
    accent: {
      50: '#F5F3FF',
      100: '#EDE9FE',
      200: '#DDD6FE',
      300: '#C4B5FD',
      400: '#A78BFA',
      500: '#8B5CF6',
      600: '#7C3AED',
      700: '#6D28D9',
      800: '#5B21B6',
      900: '#4C1D95',
      950: '#2E1065',
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
      DEFAULT: '#F8FAFC',
      subtle: '#F1F5F9',
      elevated: '#FFFFFF',
    },
    text: {
      DEFAULT: '#0F172A',
      muted: '#64748B',
      subtle: '#94A3B8',
      inverse: '#FFFFFF',
    },
    border: {
      DEFAULT: '#E2E8F0',
      strong: '#CBD5E1',
    },
    gold: '#A5B4FC',
  },
  radius: {
    none: '0px',
    sm: '6px',
    control: '10px',
    md: '12px',
    card: '16px',
    lg: '20px',
    full: '9999px',
  },
  font: {
    sans: ['var(--font-inter)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
    serif: ['var(--font-inter)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
    mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
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
    subtle: '0 1px 2px 0 rgb(15 23 42 / 0.06)',
    card: '0 4px 6px -1px rgb(15 23 42 / 0.08), 0 2px 4px -2px rgb(15 23 42 / 0.08)',
  },
};

export type TokenColors = Tokens['colors'];
export type TokenRadius = Tokens['radius'];
export type TokenFont = Tokens['font'];
