import { ShieldAlert } from 'lucide-react';
import { PRODUCT_NAME, SAFETY_LABEL } from '@/lib/constants';

/** Site footer — always carries the safety label (§2.3). */
export function Footer(): React.JSX.Element {
  return (
    <footer className="border-t border-lf-border bg-lf-canvas-subtle">
      <div className="mx-auto flex max-w-7xl flex-col gap-2 px-4 py-6 sm:flex-row sm:items-center sm:justify-between">
        <p className="font-lf-serif text-sm font-semibold text-lf-text">{PRODUCT_NAME}</p>
        <p
          className="flex items-start gap-2 text-xs text-lf-text-muted"
          role="note"
          aria-label="Safety notice"
        >
          <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{SAFETY_LABEL}</span>
        </p>
      </div>
    </footer>
  );
}
