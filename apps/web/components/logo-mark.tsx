import { useId } from 'react';

/**
 * LegacyFlow logo mark — "Lumen Aurora" (2026-10-24).
 *
 * The sprout glyph in a rounded tile filled with the signature gradient
 * (design token `--lf-gradient-primary`: #22D3EE → #0E9BBF → #7C6CF0).
 * Inline SVG so it stays crisp at every size and needs no image asset;
 * the gradient id is unique per instance via useId.
 */
export function LogoMark({ className }: { className?: string }): React.JSX.Element {
  const gradientId = useId();
  return (
    <svg viewBox="0 0 48 48" className={className} role="img" aria-label="LegacyFlow logo">
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="48" y2="48" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#22D3EE" />
          <stop offset="0.45" stopColor="#0E9BBF" />
          <stop offset="1" stopColor="#7C6CF0" />
        </linearGradient>
      </defs>
      <rect width="48" height="48" rx="13" fill={`url(#${gradientId})`} />
      <g
        transform="translate(6 6) scale(1.5)"
        fill="none"
        stroke="#FFFFFF"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M12 21c-4-3.2-7-6.6-7-10.6C5 6.6 7.8 4 11 4c.4 0 .7 0 1 .1.3-.1.6-.1 1-.1 3.2 0 6 2.6 6 6.4 0 4-3 7.4-7 10.6Z" />
        <path d="M12 21V9" />
      </g>
    </svg>
  );
}

export default LogoMark;
