import { describe, expect, it } from 'vitest';
import { documentTypeForFileName } from '../lib/document-types';

describe('documentTypeForFileName', () => {
  it('classifies the five us-va template documents', () => {
    expect(documentTypeForFileName('Last Will and Testament.pdf').key).toBe('will');
    expect(documentTypeForFileName('Advance Medical Directive.pdf').key).toBe('directive');
    expect(documentTypeForFileName('Designation of Guardian for Minor Children.pdf').key).toBe(
      'guardian',
    );
    expect(documentTypeForFileName('Durable Financial Power of Attorney.pdf').key).toBe('poa');
    expect(documentTypeForFileName('Revocable Living Trust.pdf').key).toBe('trust');
  });

  it('gives each of the five types a distinct jewel tone', () => {
    const tones = [
      documentTypeForFileName('Last Will and Testament.pdf').tone,
      documentTypeForFileName('Advance Medical Directive.pdf').tone,
      documentTypeForFileName('Designation of Guardian for Minor Children.pdf').tone,
      documentTypeForFileName('Durable Financial Power of Attorney.pdf').tone,
      documentTypeForFileName('Revocable Living Trust.pdf').tone,
    ];
    expect(new Set(tones).size).toBe(5);
  });

  it('treats a living will as a directive, not a will', () => {
    expect(documentTypeForFileName('Living Will.pdf').key).toBe('directive');
  });

  it('is case- and separator-insensitive', () => {
    expect(documentTypeForFileName('last_will_and_testament.docx').key).toBe('will');
    expect(documentTypeForFileName('REVOCABLE-LIVING-TRUST.PDF').key).toBe('trust');
    expect(documentTypeForFileName('durable-financial-power-of-attorney.docx').key).toBe('poa');
  });

  it('falls back to a neutral "other" for unknown names', () => {
    const result = documentTypeForFileName('scan-2026-10-24.pdf');
    expect(result.key).toBe('other');
    expect(result.tone).toBe('neutral');
    expect(result.label.length).toBeGreaterThan(0);
  });

  it('classifies medical powers of attorney as POA, not directives', () => {
    expect(documentTypeForFileName('Medical Power of Attorney.pdf').key).toBe('poa');
    expect(documentTypeForFileName('Health Care Power of Attorney.pdf').key).toBe('poa');
  });

  it('classifies the generator\u2019s codicil and amendment titles distinctly', () => {
    expect(documentTypeForFileName('Codicil to Last Will and Testament.pdf').key).toBe('codicil');
    expect(documentTypeForFileName('Amendment to Revocable Living Trust.pdf').key).toBe(
      'amendment',
    );
  });

  it('does not match "will" inside other words', () => {
    expect(documentTypeForFileName('Williams Family Documents.pdf').key).toBe('other');
  });
});
