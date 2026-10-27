import type { Metadata, Viewport } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import '@legacyflow/design-tokens/tokens.css';
import './globals.css';
import { PRODUCT_NAME } from '@/lib/constants';

const geist = Geist({
  subsets: ['latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-geist',
  display: 'swap',
});

const geistMono = Geist_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-geist-mono',
  display: 'swap',
});

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  // Chat-style screens (the AI interview) anchor their composer to the
  // dynamic viewport; when the on-screen keyboard opens, the layout
  // viewport must resize with it instead of the keyboard overlaying the
  // composer (Chrome/Android; iOS Safari tracks the visual viewport).
  interactiveWidget: 'resizes-content',
};

export const metadata: Metadata = {
  title: {
    default: `${PRODUCT_NAME} — Estate planning, attorney-guided`,
    template: `%s — ${PRODUCT_NAME}`,
  },
  description:
    'LegacyFlow helps families organize their estate plan with attorney guidance. Attorney review pending.',
};

/**
 * Pre-paint theme init: applies the `.dark` class (Noir Desk tokens) before
 * first render so there is no light-flash for dark-mode users. Resolution:
 * stored `lf-theme` choice → OS preference → light (Atelier Light default).
 */
const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem('lf-theme');if(t==='dark'||(!t&&window.matchMedia('(prefers-color-scheme: dark)').matches)){document.documentElement.classList.add('dark');}}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }): React.JSX.Element {
  return (
    <html lang="en" className={`${geist.variable} ${geistMono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lf-control focus:bg-lf-primary-800 dark:focus:text-lf-primary-950 focus:px-4 focus:py-2 focus:text-lf-primary-foreground"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
