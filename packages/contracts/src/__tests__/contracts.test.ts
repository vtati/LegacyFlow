import { describe, expect, it } from 'vitest';
import { isFirmRole, isJurisdictionPackStatus, isMatter } from '../index';
import type { Matter } from '../index';

describe('contracts', () => {
  it('validates a sample Matter at runtime', () => {
    const sample: Matter = {
      id: 'matter_01',
      firmId: 'firm_01',
      householdId: 'household_01',
      jurisdictionPackId: 'pack_va_v1',
      status: 'attorney_review',
      createdAt: '2026-09-26T00:00:00.000Z',
      updatedAt: '2026-09-26T00:00:00.000Z',
    };
    expect(isMatter(sample)).toBe(true);
  });

  it('rejects malformed Matter payloads', () => {
    expect(isMatter(null)).toBe(false);
    expect(isMatter({ id: 'x' })).toBe(false);
    expect(isMatter({ id: 'x', firmId: 'f', householdId: 'h', jurisdictionPackId: 'p', status: 'bogus', createdAt: 't', updatedAt: 't' })).toBe(false);
  });

  it('guards role and pack-status unions', () => {
    expect(isFirmRole('attorney')).toBe(true);
    expect(isFirmRole('superadmin')).toBe(false);
    expect(isJurisdictionPackStatus('approved')).toBe(true);
    expect(isJurisdictionPackStatus('published')).toBe(false);
  });
});
