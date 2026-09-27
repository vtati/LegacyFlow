import type { Config } from 'tailwindcss';

/**
 * LegacyFlow Tailwind v3 preset.
 * Maps the design tokens (§5.2) onto Tailwind theme keys via the `--lf-*`
 * CSS variables, so semantic utilities (`bg-lf-primary-800`, `rounded-lf-card`)
 * track the tokens at runtime.
 */
export const legacyflowPreset: Partial<Config> = {
  theme: {
    extend: {
      colors: {
        'lf-primary': {
          50: 'var(--lf-primary-50)',
          100: 'var(--lf-primary-100)',
          200: 'var(--lf-primary-200)',
          300: 'var(--lf-primary-300)',
          400: 'var(--lf-primary-400)',
          500: 'var(--lf-primary-500)',
          600: 'var(--lf-primary-600)',
          700: 'var(--lf-primary-700)',
          800: 'var(--lf-primary-800)',
          900: 'var(--lf-primary-900)',
          950: 'var(--lf-primary-950)',
          foreground: 'var(--lf-primary-foreground)',
        },
        'lf-success': {
          50: 'var(--lf-success-50)',
          100: 'var(--lf-success-100)',
          200: 'var(--lf-success-200)',
          300: 'var(--lf-success-300)',
          400: 'var(--lf-success-400)',
          500: 'var(--lf-success-500)',
          600: 'var(--lf-success-600)',
          700: 'var(--lf-success-700)',
          800: 'var(--lf-success-800)',
          900: 'var(--lf-success-900)',
          950: 'var(--lf-success-950)',
          foreground: 'var(--lf-success-foreground)',
        },
        'lf-accent': {
          50: 'var(--lf-accent-50)',
          100: 'var(--lf-accent-100)',
          200: 'var(--lf-accent-200)',
          300: 'var(--lf-accent-300)',
          400: 'var(--lf-accent-400)',
          500: 'var(--lf-accent-500)',
          600: 'var(--lf-accent-600)',
          700: 'var(--lf-accent-700)',
          800: 'var(--lf-accent-800)',
          900: 'var(--lf-accent-900)',
          950: 'var(--lf-accent-950)',
          foreground: 'var(--lf-accent-foreground)',
        },
        'lf-error': {
          50: 'var(--lf-error-50)',
          100: 'var(--lf-error-100)',
          200: 'var(--lf-error-200)',
          300: 'var(--lf-error-300)',
          400: 'var(--lf-error-400)',
          500: 'var(--lf-error-500)',
          600: 'var(--lf-error-600)',
          700: 'var(--lf-error-700)',
          800: 'var(--lf-error-800)',
          900: 'var(--lf-error-900)',
          950: 'var(--lf-error-950)',
          foreground: 'var(--lf-error-foreground)',
        },
        'lf-canvas': {
          DEFAULT: 'var(--lf-canvas)',
          subtle: 'var(--lf-canvas-subtle)',
          elevated: 'var(--lf-canvas-elevated)',
        },
        'lf-text': {
          DEFAULT: 'var(--lf-text)',
          muted: 'var(--lf-text-muted)',
          subtle: 'var(--lf-text-subtle)',
          inverse: 'var(--lf-text-inverse)',
        },
        'lf-border': {
          DEFAULT: 'var(--lf-border)',
          strong: 'var(--lf-border-strong)',
        },
      },
      borderRadius: {
        'lf-none': 'var(--lf-radius-none)',
        'lf-sm': 'var(--lf-radius-sm)',
        'lf-control': 'var(--lf-radius-control)',
        'lf-md': 'var(--lf-radius-md)',
        'lf-card': 'var(--lf-radius-card)',
        'lf-lg': 'var(--lf-radius-lg)',
        'lf-full': 'var(--lf-radius-full)',
      },
      fontFamily: {
        'lf-sans': 'var(--lf-font-sans)',
        'lf-serif': 'var(--lf-font-serif)',
        'lf-mono': 'var(--lf-font-mono)',
      },
      boxShadow: {
        'lf-subtle': 'var(--lf-shadow-subtle)',
        'lf-card': 'var(--lf-shadow-card)',
      },
    },
  },
};

export default legacyflowPreset;
