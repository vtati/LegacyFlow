/**
 * LegacyFlow design tokens — TypeScript source of truth.
 *
 * Mirrors the Heritage Editorial palette: warm paper canvas, warm charcoal
 * ink, terracotta accent, sage success, warm amber warning, warm red error.
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
    /** Primary brand: warm charcoal ramp. */
    primary: ColorScale & { foreground: string };
    /** Success/support: sage family. */
    success: ColorScale & { foreground: string };
    /** Action accent: terracotta family. */
    accent: ColorScale & { foreground: string };
    /** Error: warm red — never the only communication channel. */
    error: ColorScale & { foreground: string };
    /** Warning: warm amber family (50–700). */
    warning: WarningScale & { foreground: string };
    /** Canvas: warm paper surfaces. */
    canvas: { DEFAULT: string; subtle: string; elevated: string };
    /** Text: warm charcoal ink. */
    text: { DEFAULT: string; muted: string; subtle: string; inverse: string };
    /** Borders: warm hairlines. */
    border: { DEFAULT: string; strong: string };
    /** Gold: landing page only. */
    gold: string;
  };
  /** 12–16px cards; 8–10px controls. */
  radius: {
    none: string;
    sm: string;
    /** Controls (8px, within the 8–10px band). */
    control: string;
    md: string;
    /** Cards (14px, within the 12–16px band). */
    card: string;
    lg: string;
    full: string;
  };
  font: {
    /** Inter via next/font (§5.2); system fallbacks keep rendering deterministic. */
    sans: string[];
    /** Fraunces via next/font; editorial serif for marketing/onboarding headings. */
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
  /** Low-contrast shadows only; no excessive floating-card look. */
  shadow: { none: string; subtle: string; card: string };
}

export const tokens: Tokens = {
  colors: {
    primary: {
      50: '#F5F2EA',
      100: '#ECE6D6',
      200: '#D9D0B8',
      300: '#C2B694',
      400: '#A29878',
      500: '#84785E',
      600: '#645A48',
      700: '#4A4335',
      800: '#2E2A22',
      900: '#1C1917',
      950: '#12100D',
      foreground: '#FAF7F1',
    },
    success: {
      50: '#F0F6F2',
      100: '#E4EFE9',
      200: '#C9E1D4',
      300: '#A4C9B6',
      400: '#79AD94',
      500: '#5D9078',
      600: '#4D7C6B',
      700: '#3D6659',
      800: '#325046',
      900: '#2A3F38',
      950: '#172722',
      foreground: '#FFFFFF',
    },
    accent: {
      50: '#FBF1E8',
      100: '#F7E3D7',
      200: '#F0CFB0',
      300: '#E6AD84',
      400: '#D6865A',
      500: '#C15F3C',
      600: '#A94F31',
      700: '#8A3E26',
      800: '#6F3220',
      900: '#56281B',
      950: '#3B1B12',
      foreground: '#FFFFFF',
    },
    error: {
      50: '#FDF3EE',
      100: '#FBEAE4',
      200: '#F7D6C6',
      300: '#F0AE94',
      400: '#E48360',
      500: '#D65E35',
      600: '#C2410C',
      700: '#9A3412',
      800: '#7D2C10',
      900: '#63250E',
      950: '#40170A',
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
    control: '8px',
    md: '12px',
    card: '14px',
    lg: '16px',
    full: '9999px',
  },
  font: {
    sans: ['var(--font-inter)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
    serif: ['var(--font-fraunces)', 'ui-serif', 'Georgia', 'serif'],
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
    subtle: '0 1px 2px 0 rgb(15 23 42 / 0.05)',
    card: '0 1px 3px 0 rgb(15 23 42 / 0.08), 0 1px 2px -1px rgb(15 23 42 / 0.08)',
  },
};

export type TokenColors = Tokens['colors'];
export type TokenRadius = Tokens['radius'];
export type TokenFont = Tokens['font'];
