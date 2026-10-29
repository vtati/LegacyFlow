/**
 * Open-redirect guard for post-auth `next` parameters (SEC-010).
 *
 * Accepts only same-origin relative paths: the value must start with a
 * single `/` (not `//`, which browsers resolve protocol-relative to
 * another host, and not `/\`, which some browsers normalize the same
 * way). Anything else — absolute URLs, bare paths, missing values —
 * falls back to the default landing page.
 *
 * The value must also be free of whitespace and control characters
 * anywhere: the WHATWG URL parser strips ASCII tab/newline before
 * parsing, so `/` + LF + `/evil.example` would otherwise resolve to
 * https://evil.example/ even though it passes the `//` check.
 */
export const DEFAULT_NEXT_PATH = '/family/home';

const WHITESPACE_OR_CONTROL = /[\s\x00-\x1f\x7f]/;

export function safeNextPath(
  value: string | null | undefined,
  fallback: string = DEFAULT_NEXT_PATH,
): string {
  if (!value) return fallback;
  if (WHITESPACE_OR_CONTROL.test(value)) return fallback;
  if (!value.startsWith('/')) return fallback;
  if (value.startsWith('//') || value.startsWith('/\\')) return fallback;
  return value;
}
