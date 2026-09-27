import type { Config } from 'tailwindcss';
import { legacyflowPreset } from '@legacyflow/design-tokens/tailwind-preset';

const config: Config = {
  presets: [legacyflowPreset],
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}', './middleware.ts'],
  theme: {
    extend: {},
  },
  plugins: [],
};

export default config;
