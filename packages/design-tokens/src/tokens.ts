/**
 * LegacyFlow design tokens — TypeScript source of truth.
 *
 * Mirrors the visual system in the product artifacts (§5.2):
 * calm, private, supportive family surface + fast, review-oriented attorney surface.
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

export interface Tokens {
  colors: {
    /** Primary brand: indigo/navy family (§5.2). */
    primary: ColorScale & { foreground: string };
    /** Success/support: muted teal/sage family (§5.2). */
    success: ColorScale & { foreground: string };
    /** Action accent: warm amber/terracotta family (§5.2). */
    accent: ColorScale & { foreground: string };
    /** Error: accessible red — never the only communication channel (§5.2). */
    error: ColorScale & { foreground: string };
    /** Canvas: warm off-white surfaces (§5.2). */
    canvas: { DEFAULT: string; subtle: string; elevated: string };
    /** Text: slate; body contrast at WCAG AA or better (§5.2). */
    text: { DEFAULT: string; muted: string; subtle: string; inverse: string };
    /** Borders: thin, low-contrast (§5.2 elevation). */
    border: { DEFAULT: string; strong: string };
  };
  /** 12–16px cards; 8–10px controls (§5.2). */
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
    /** Geist or Inter (§5.2); system fallbacks keep rendering deterministic. */
    sans: string[];
    /** Optional editorial serif, marketing/onboarding headings only. */
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
  /** Low-contrast shadows only; no excessive floating-card look (§5.2). */
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
      800: '#1E3A8A',
      900: '#1E2A5A',
      950: '#172554',
      foreground: '#FFFFFF',
    },
    success: {
      50: '#F0FDFA',
      100: '#CCFBF1',
      200: '#99F6E4',
      300: '#5EEAD4',
      400: '#2DD4BF',
      500: '#14B8A6',
      600: '#2D7A6B',
      700: '#0F766E',
      800: '#115E59',
      900: '#134E4A',
      950: '#042F2E',
      foreground: '#FFFFFF',
    },
    accent: {
      50: '#FFFBEB',
      100: '#FEF3C7',
      200: '#FDE68A',
      300: '#FCD34D',
      400: '#FBBF24',
      500: '#F59E0B',
      600: '#D97706',
      700: '#B45309',
      800: '#C2410C',
      900: '#7C2D12',
      950: '#431407',
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
    canvas: {
      DEFAULT: '#FAFAF7',
      subtle: '#F8FAFC',
      elevated: '#FFFFFF',
    },
    text: {
      DEFAULT: '#0F172A',
      muted: '#334155',
      subtle: '#64748B',
      inverse: '#F8FAFC',
    },
    border: {
      DEFAULT: '#E2E8F0',
      strong: '#CBD5E1',
    },
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
    sans: ['Geist', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
    serif: ['Georgia', 'ui-serif', 'serif'],
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
