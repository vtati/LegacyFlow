import type { Metadata } from 'next';
import { Fraunces, Inter } from 'next/font/google';
import '@legacyflow/design-tokens/tokens.css';
import './globals.css';
import { PRODUCT_NAME } from '@/lib/constants';

const inter = Inter({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-inter',
  display: 'swap',
});

const fraunces = Fraunces({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-fraunces',
  display: 'swap',
});

export const metadata: Metadata = {
  title: {
    default: `${PRODUCT_NAME} — Estate planning, attorney-guided`,
    template: `%s — ${PRODUCT_NAME}`,
  },
  description:
    'LegacyFlow helps families organize their estate plan with attorney guidance. Attorney review pending.',
};

export default function RootLayout({ children }: { children: React.ReactNode }): React.JSX.Element {
  return (
    <html lang="en" className={`${inter.variable} ${fraunces.variable}`}>
      <body>
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lf-control focus:bg-lf-primary-800 focus:px-4 focus:py-2 focus:text-lf-primary-foreground"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
