'use client';

import { useCallback, useEffect, useState } from 'react';
import { AttorneyReviewPanel } from '@/components/family/review/attorney-review-panel';
import { firmApi, type AttorneyReviewFindings } from '@/lib/api-client';

/**
 * Firm-side automated pre-review section for the review packet tab:
 * the findings that traveled with the family's submitted drafts, with
 * the attorney's acknowledgment (override) recorded on each finding.
 * Advisory only — the release gate is a separate surface and is not
 * affected by anything here.
 */
export function AttorneyReviewSection({
  matterId,
}: {
  matterId: string;
}): React.JSX.Element | null {
  const [data, setData] = useState<AttorneyReviewFindings | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await firmApi.getAttorneyReviewFindings(matterId));
    } catch {
      /* section only; the packet surface carries its own errors */
    }
  }, [matterId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleAcknowledge = useCallback(
    async (findingId: string, note: string) => {
      setActionError(null);
      try {
        await firmApi.acknowledgeAttorneyReviewFinding(matterId, findingId, note);
        await refresh();
      } catch {
        setActionError('That acknowledgment could not be recorded — please try again.');
      }
    },
    [matterId, refresh],
  );

  const handleRun = useCallback(async () => {
    setActionError(null);
    try {
      await firmApi.runAttorneyReview(matterId);
      await refresh();
    } catch {
      setActionError('The pre-review could not be run — please try again.');
    }
  }, [matterId, refresh]);

  if (!data || !data.enabled) return null;
  return (
    <AttorneyReviewPanel
      data={data}
      variant="firm"
      onAcknowledge={handleAcknowledge}
      onRun={handleRun}
      actionError={actionError}
    />
  );
}
