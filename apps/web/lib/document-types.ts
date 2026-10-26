/**
 * Document-type classification for draft documents (Lumen Aurora Phase 3).
 *
 * DraftDocument carries no type field, so the family documents view derives
 * a display type from the generated file name (which mirrors the generator's
 * template titles in the API's documents service).
 *
 * Tile colors here are CATEGORICAL identity (the deck's jewel tiles), not
 * state semantics: they name which document family a file belongs to, the
 * way a folder color would. State meaning (confirmed / attention / blocked)
 * is carried by StatusPill only — never infer it from a tile color.
 *
 * Match order matters:
 * - a codicil amends a will but is its own instrument;
 * - "Medical Power of Attorney" / "Health Care Power of Attorney" are POAs,
 *   so the POA check runs before the directive terms that also match them;
 * - an amendment is checked before the instrument it amends;
 * - "living will" is a directive, and `will` matches on word boundaries
 *   only (never inside "Williams").
 */

export type DocumentTone = 'violet' | 'mint' | 'sky' | 'cyan' | 'amber' | 'navy' | 'neutral';

export interface DocumentType {
  key: 'will' | 'codicil' | 'directive' | 'guardian' | 'poa' | 'trust' | 'amendment' | 'other';
  label: string;
  tone: DocumentTone;
}

const TYPES = {
  will: { key: 'will', label: 'Last will', tone: 'navy' },
  codicil: { key: 'codicil', label: 'Codicil', tone: 'navy' },
  directive: { key: 'directive', label: 'Advance directive', tone: 'mint' },
  guardian: { key: 'guardian', label: 'Guardian designation', tone: 'sky' },
  poa: { key: 'poa', label: 'Power of attorney', tone: 'cyan' },
  trust: { key: 'trust', label: 'Living trust', tone: 'amber' },
  amendment: { key: 'amendment', label: 'Amendment', tone: 'amber' },
  other: { key: 'other', label: 'Document', tone: 'neutral' },
} as const satisfies Record<string, DocumentType>;

export function documentTypeForFileName(fileName: string): DocumentType {
  const name = fileName.toLowerCase().replace(/[_-]+/g, ' ');
  if (name.includes('codicil')) return TYPES.codicil;
  if (name.includes('guardian')) return TYPES.guardian;
  if (name.includes('power of attorney') || /\bpoa\b/.test(name)) return TYPES.poa;
  if (name.includes('amendment')) return TYPES.amendment;
  if (
    name.includes('advance') ||
    name.includes('directive') ||
    name.includes('medical') ||
    name.includes('living will') ||
    name.includes('health care') ||
    name.includes('healthcare')
  ) {
    return TYPES.directive;
  }
  if (name.includes('trust')) return TYPES.trust;
  if (/\bwill\b/.test(name) || name.includes('testament')) return TYPES.will;
  return TYPES.other;
}
