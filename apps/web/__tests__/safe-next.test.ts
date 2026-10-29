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

  it('accepts legitimate relative paths, preserving query strings', () => {
    expect(safeNextPath('/family/home')).toBe('/family/home');
    expect(safeNextPath('/firm/matters?status=open')).toBe('/firm/matters?status=open');
    expect(safeNextPath('/auth/update-password')).toBe('/auth/update-password');
  });

  it('falls back for missing or empty values', () => {
    expect(safeNextPath(null)).toBe('/family/home');
    expect(safeNextPath(undefined)).toBe('/family/home');
    expect(safeNextPath('')).toBe('/family/home');
    expect(safeNextPath('family/home')).toBe('/family/home');
  });
});
