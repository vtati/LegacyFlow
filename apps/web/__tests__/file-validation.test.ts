/** @vitest-environment jsdom */
import { describe, expect, it } from 'vitest';
import { MAX_FILE_SIZE_BYTES, validateFile } from '../components/files/upload-button';

function makeFile(name: string, type: string, size: number): File {
  return new File([new Uint8Array(size)], name, { type });
}

describe('validateFile', () => {
  it('accepts allowed types under the size cap', () => {
    expect(validateFile(makeFile('deed.pdf', 'application/pdf', 1024))).toBeNull();
    expect(validateFile(makeFile('photo.jpg', 'image/jpeg', MAX_FILE_SIZE_BYTES - 1))).toBeNull();
  });

  it('rejects disallowed types', () => {
    const error = validateFile(makeFile('notes.txt', 'text/plain', 100));
    expect(error).toContain('not an accepted type');
  });

  it('rejects oversized files', () => {
    const error = validateFile(makeFile('big.pdf', 'application/pdf', MAX_FILE_SIZE_BYTES + 1));
    expect(error).toContain('too large');
  });

  it('rejects empty files', () => {
    const error = validateFile(makeFile('empty.pdf', 'application/pdf', 0));
    expect(error).toContain('empty');
  });
});
