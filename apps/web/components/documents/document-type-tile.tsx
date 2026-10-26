import { Baby, FileText, HeartPulse, KeyRound, Landmark, PenLine, ScrollText, type LucideIcon } from 'lucide-react';
import { documentTypeForFileName, type DocumentTone } from '@/lib/document-types';
import { cn } from '@/lib/utils';

/**
 * Colored document-type tile (Lumen Aurora Phase 3): each document family
 * gets a jewel tile — navy will/codicil, mint directive, sky guardian,
 * cyan POA, amber trust/amendment. Tile color is categorical identity
 * (which document family), never state — see lib/document-types. Text
 * steps are the AA-verified 700s on the matching 100 tints.
 */
const TONE_TILE: Record<DocumentTone, string> = {
  violet: 'bg-lf-violet-100 text-lf-violet-700',
  mint: 'bg-lf-success-100 text-lf-success-700',
  sky: 'bg-lf-sky-100 text-lf-sky-700',
  cyan: 'bg-lf-accent-100 text-lf-accent-700',
  amber: 'bg-lf-warning-100 text-lf-warning-700',
  navy: 'bg-lf-primary-100 text-lf-primary-700',
  neutral: 'bg-lf-primary-100 text-lf-primary-700',
};

const TYPE_ICON: Record<string, LucideIcon> = {
  will: ScrollText,
  codicil: PenLine,
  directive: HeartPulse,
  guardian: Baby,
  poa: KeyRound,
  trust: Landmark,
  amendment: PenLine,
  other: FileText,
};

export function DocumentTypeTile({
  fileName,
  className,
}: {
  fileName: string;
  className?: string;
}): React.JSX.Element {
  const type = documentTypeForFileName(fileName);
  const Icon = TYPE_ICON[type.key] ?? FileText;
  return (
    <span
      className={cn(
        'flex size-11 shrink-0 items-center justify-center rounded-lf-control',
        TONE_TILE[type.tone],
        className,
      )}
      aria-hidden="true"
    >
      <Icon className="size-5" />
    </span>
  );
}

/** The display label for a document's derived type (e.g. "Living trust"). */
export function documentTypeLabel(fileName: string): string {
  return documentTypeForFileName(fileName).label;
}
