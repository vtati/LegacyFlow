/**
 * SEC-010: the auth callback's `next` parameter was used unsanitized in
 * a redirect — an absolute or protocol-relative URL bounced a
 * just-signed-in user off-origin (open redirect / phishing). Only
 * same-origin relative paths are accepted.
 */
import { describe, expect, it } from 'vitest';
import { safeNextPath } from '../lib/auth/safe-next';

describe('safeNextPath (SEC-010)', () => {
  it('rejects absolute URLs', () => {
    expect(safeNextPath('https://evil.example/phish')).toBe('/family/home');
    expect(safeNextPath('http://evil.example')).toBe('/family/home');
  });

  it('rejects protocol-relative URLs', () => {
    expect(safeNextPath('//evil.example/phish')).toBe('/family/home');
    expect(safeNextPath('/\\evil.example')).toBe('/family/home');
  });

  it('rejects control-character smuggling (WHATWG URL strips tab/newline)', () => {
    // `new URL('/\n/evil.example', origin)` parses to https://evil.example/
    // because the WHATWG parser removes ASCII tab/newline before parsing.
    expect(safeNextPath('/\t/evil.example')).toBe('/family/home');
    expect(safeNextPath('/\n/evil.example')).toBe('/family/home');
    expect(safeNextPath('/\r/evil.example')).toBe('/family/home');
    // The %0A-decoded form, as it arrives from ?next=%2F%0A%2Fevil.example.
    expect(safeNextPath(decodeURIComponent('%2F%0A%2Fevil.example'))).toBe('/family/home');
    // Whitespace/control characters anywhere in the value are rejected,
    // not just in the authority position.
    expect(safeNextPath('/family/home next')).toBe('/family/home');
    expect(safeNextPath('/family/\u0000home')).toBe('/family/home');
    expect(safeNextPath('/family/home\u007f')).toBe('/family/home');
  });

  it('accepts legitimate relative paths, preserving query strings', () => {
    expect(safeNextPath('/family/home')).toBe('/family/home');
    expect(safeNextPath('/family')).toBe('/family');
    expect(safeNextPath('/firm/matters?x=1')).toBe('/firm/matters?x=1');
    expect(safeNextPath('/firm/matters?status=open')).toBe('/firm/matters?status=open');
    expect(safeNextPath('/auth/update-password')).toBe('/auth/update-password');
    expect(safeNextPath('/')).toBe('/');
  });

  it('falls back for missing or empty values', () => {
    expect(safeNextPath(null)).toBe('/family/home');
    expect(safeNextPath(undefined)).toBe('/family/home');
    expect(safeNextPath('')).toBe('/family/home');
    expect(safeNextPath('family/home')).toBe('/family/home');
  });
});
