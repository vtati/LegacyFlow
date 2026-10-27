'use client';

import Link from 'next/link';
import { use, useCallback, useEffect, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { EmptyState, ErrorState, LoadingState } from '@/components/family/page-state';
import { AssistantPanel } from '@/components/ai/assistant-panel';
import { AssignAttorney } from '@/components/firm/assign-attorney';
import { ReviewDecisionPanel } from '@/components/firm/review-decision-panel';
import { MatterOverview } from '@/components/firm/matter-overview';
import { ReviewPacketTab } from '@/components/firm/review-packet-tab';
import { CommentsTab } from '@/components/firm/comments-tab';
import { ClarificationsTab } from '@/components/firm/clarifications-tab';
import { GatesTab } from '@/components/firm/gates-tab';
import { FilesTab } from '@/components/firm/files-tab';
import { DocumentsTab } from '@/components/firm/documents-tab';
import { SigningTab } from '@/components/firm/signing-tab';
import { FundingTab } from '@/components/firm/funding-tab';
import { api, firmApi, type HouseholdResponse, type MatterDetail } from '@/lib/api-client';
import { formatStatus, statusBadgeClass } from '@/lib/format';
import { cn } from '@/lib/utils';

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'files', label: 'Files' },
  { id: 'documents', label: 'Documents' },
  { id: 'signing', label: 'Signing' },
  { id: 'funding', label: 'Funding' },
  { id: 'assistant', label: 'Guided answers' },
  { id: 'review', label: 'Review packet' },
  { id: 'comments', label: 'Comments' },
  { id: 'clarifications', label: 'Clarifications' },
  { id: 'gates', label: 'Approval gates' },
] as const;

type TabId = (typeof TABS)[number]['id'];

interface FirmMatterDetailPageProps {
  params: Promise<{ id: string }>;
}

/** Firm matter detail — overview, review packets, comments, clarifications, gates. */
export default function FirmMatterDetailPage({
  params,
}: FirmMatterDetailPageProps): React.JSX.Element {
  const { id: matterId } = use(params);
  const [tab, setTab] = useState<TabId>('overview');
  const [matter, setMatter] = useState<MatterDetail | null>(null);
  const [household, setHousehold] = useState<HouseholdResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    firmApi
      .getMatter(matterId)
      .then((detail) => {
        setMatter(detail);
        api
          .getHousehold(matterId)
          .then(setHousehold)
          .catch(() => setHousehold(null));
      })
      .catch((err: unknown) => setError(err))
      .finally(() => setLoading(false));
  }, [matterId]);

  // Gate decisions in the Gates tab can transition the matter server-side
  // (e.g. approving document_release → DOCUMENTS_RELEASED). Refetch so the
  // header badge, the decision panel, and the Gates tab's matterStatus prop
  // all reflect the new status instead of going stale.
  const handleMatterChanged = useCallback(() => {
    firmApi
      .getMatter(matterId)
      .then(setMatter)
      .catch(() => undefined);
  }, [matterId]);

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <Link
        href="/firm/matters"
        className="inline-flex items-center gap-1.5 text-sm font-medium text-lf-primary-700 underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500 rounded-lf-control"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        Back to matters
      </Link>

      {loading && <LoadingState lines={5} label="Loading matter…" />}

      {error != null && !loading ? (
        <ErrorState error={error} onRetry={() => window.location.reload()} />
      ) : null}

      {!loading && !error && matter === null && (
        <EmptyState
          title="Matter not found"
          description="This matter does not exist or you do not have access to it."
        />
      )}

      {!loading && !error && matter !== null && (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h1 className="font-lf-serif text-3xl font-semibold tracking-tight text-lf-text">
                {matter.matter.title}
              </h1>
              <p className="mt-2 text-sm text-lf-text-muted">
                {matter.household?.name ?? 'Household'} · {matter.matter.jurisdiction_code}
              </p>
            </div>
            <div className="flex flex-col items-end gap-3">
              <span
                className={cn(
                  'shrink-0 rounded-full px-3 py-1 text-xs font-medium',
                  statusBadgeClass(matter.matter.status),
                )}
              >
                {formatStatus(matter.matter.status)}
              </span>
              <AssignAttorney
                matterId={matterId}
                currentAttorneyUserId={matter.matter.assigned_attorney_id}
                onAssigned={(attorneyUserId) =>
                  setMatter((prev) =>
                    prev === null
                      ? prev
                      : {
                          ...prev,
                          matter: { ...prev.matter, assigned_attorney_id: attorneyUserId },
                        },
                  )
                }
              />
            </div>
          </div>

          <ReviewDecisionPanel
            matterId={matterId}
            status={matter.matter.status}
            onStatusChange={(newStatus) =>
              setMatter((prev) =>
                prev === null
                  ? prev
                  : {
                      ...prev,
                      matter: { ...prev.matter, status: newStatus as typeof prev.matter.status },
                    },
              )
            }
          />

          <div
            role="tablist"
            aria-label="Matter sections"
            className="flex flex-wrap gap-1 border-b border-lf-border"
          >
            {TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={tab === item.id}
                aria-controls={`tab-panel-${item.id}`}
                id={`tab-${item.id}`}
                onClick={() => setTab(item.id)}
                className={cn(
                  '-mb-px border-b-2 px-4 py-2.5 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500',
                  tab === item.id
                    ? 'border-lf-primary-600 text-lf-primary-700'
                    : 'border-transparent text-lf-text-muted hover:border-lf-border hover:text-lf-text',
                )}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div
            role="tabpanel"
            id={`tab-panel-${tab}`}
            aria-labelledby={`tab-${tab}`}
            className="pt-2"
          >
            {tab === 'overview' && <MatterOverview matter={matter} household={household} />}
            {tab === 'files' && <FilesTab matterId={matterId} />}
            {tab === 'documents' && <DocumentsTab matterId={matterId} />}
            {tab === 'signing' && <SigningTab matterId={matterId} />}
            {tab === 'funding' && <FundingTab matterId={matterId} />}
            {tab === 'assistant' && (
              <AssistantPanel
                panelId="firm-assistant"
                ask={(question) => firmApi.askAi(matterId, question)}
              />
            )}
            {tab === 'review' && <ReviewPacketTab matterId={matterId} />}
            {tab === 'comments' && <CommentsTab matterId={matterId} />}
            {tab === 'clarifications' && <ClarificationsTab matterId={matterId} />}
            {tab === 'gates' && (
              <GatesTab
                matterId={matterId}
                matterStatus={matter.matter.status}
                onMatterChanged={handleMatterChanged}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}
