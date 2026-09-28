'use client';

import { AdminTemplatesSection } from '@/components/admin/template-library';
import { FirmScopeNote } from '@/components/admin/admin-page-state';

/** Admin → Templates: the firm's document template library. */
export default function AdminTemplatesPage(): React.JSX.Element {
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-lf-text">Templates</h1>
        <p className="mt-1 text-sm text-lf-text-muted">
          The document templates your firm&apos;s attorneys assemble plans from.
        </p>
      </div>

      <FirmScopeNote>
        This is your firm&apos;s library — the same templates the firm workspace uses.
      </FirmScopeNote>

      <AdminTemplatesSection />
    </div>
  );
}
