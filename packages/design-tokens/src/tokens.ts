/**
 * LegacyFlow design tokens — TypeScript source of truth.
 *
 * Heritage Editorial palette: warm ivory canvas, warm-charcoal ink primary,
 * terracotta accent, sage success, warm red error, warm amber warning.
 * Fraunces (display serif) + Inter (UI sans).
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
    /** Primary brand: warm charcoal ramp — 900 is ink, CTAs are near-black with ivory text. */
    primary: ColorScale & { foreground: string };
    /** Success/support: sage family. */
    success: ColorScale & { foreground: string };
    /** Action accent: terracotta family — the ONE signature accent. */
    accent: ColorScale & { foreground: string };
    /** Error: warm red — never the only communication channel. */
    error: ColorScale & { foreground: string };
    /** Warning: warm amber family (50–700). */
    warning: WarningScale & { foreground: string };
    /** Canvas: warm ivory surfaces. */
    canvas: { DEFAULT: string; subtle: string; elevated: string };
    /** Text: warm ink. */
    text: { DEFAULT: string; muted: string; subtle: string; inverse: string };
    /** Borders: warm hairlines. */
    border: { DEFAULT: string; strong: string };
    /** Gold: reserved for landing-page flourishes + "plan complete" moments only. */
    gold: string;
  };
  /** 14px cards; 8–10px controls. */
  radius: {
    none: string;
    sm: string;
    /** Controls (10px). */
    control: string;
    md: string;
    /** Cards (14px). */
    card: string;
    lg: string;
    full: string;
  };
  font: {
    /** Inter via next/font (§5.2); system fallbacks keep rendering deterministic. */
    sans: string[];
    /** Fraunces display serif via next/font — headlines, page titles, empty states. */
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
  /** Thin warm borders do the work; one soft warm shadow for overlays. */
  shadow: { none: string; subtle: string; card: string };
}

export const tokens: Tokens = {
  colors: {
    primary: {
      50: '#F5F2EA',
      100: '#EAE4D3',
      200: '#D9D1BE',
      300: '#B8AC93',
      400: '#8A7D65',
      500: '#6B5F4B',
      600: '#4A4234',
      700: '#353026',
      800: '#26221B',
      900: '#1C1917',
      950: '#12100D',
      foreground: '#FAF7F1',
    },
    success: {
      50: '#F0F5F2',
      100: '#E4EFE9',
      200: '#C9DDD3',
      300: '#A3C2B4',
      400: '#7AA392',
      500: '#5E8A78',
      600: '#4D7C6B',
      700: '#3D6659',
      800: '#33544A',
      900: '#2A443C',
      950: '#162825',
      foreground: '#FFFFFF',
    },
    accent: {
      50: '#FBF1EA',
      100: '#F7E3D7',
      200: '#F0CDB8',
      300: '#E5A985',
      400: '#D47F56',
      500: '#C15F3C',
      600: '#A94F31',
      700: '#8A3E26',
      800: '#6E3220',
      900: '#57281B',
      950: '#3A1A12',
      foreground: '#FFFFFF',
    },
    error: {
      50: '#FBEAE4',
      100: '#F8D9CC',
      200: '#F0B49E',
      300: '#E58A6B',
      400: '#D9613F',
      500: '#C9501F',
      600: '#A93E12',
      700: '#8A3210',
      800: '#6E2A12',
      900: '#571F0F',
      950: '#3A140A',
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
      DEFAULT: '#FAF7F1',
      subtle: '#F3EEE3',
      elevated: '#FFFFFF',
    },
    text: {
      DEFAULT: '#1C1917',
      muted: '#57534E',
      subtle: '#78716C',
      inverse: '#FAF7F1',
    },
    border: {
      DEFAULT: '#E8E2D5',
      strong: '#D9D1BE',
    },
    gold: '#A98A3D',
  },
  radius: {
    none: '0px',
    sm: '6px',
    control: '10px',
    md: '12px',
    card: '14px',
    lg: '20px',
    full: '9999px',
  },
  font: {
    sans: ['var(--font-inter)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
    serif: ['var(--font-fraunces)', 'Georgia', 'serif'],
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
    subtle: '0 1px 2px 0 rgb(28 25 23 / 0.06)',
    card: '0 8px 24px rgb(28 25 23 / 0.08)',
  },
};

export type TokenColors = Tokens['colors'];
export type TokenRadius = Tokens['radius'];
export type TokenFont = Tokens['font'];
