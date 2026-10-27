'use client';

import { useState } from 'react';
import { ChevronDown, Download, FileText, Plus, Sparkles } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/family/page-state';
import { api, type FileKeyFact, type FileScanStatus, type MatterFile } from '@/lib/api-client';
import { formatDateTime } from '@/lib/format';
import { cn } from '@/lib/utils';

export function scanBadgeClass(status: FileScanStatus): string {
  const normalized = status.toLowerCase();
  if (normalized === 'clean') return 'bg-lf-success-100 text-lf-success-700';
  if (normalized === 'quarantined') return 'bg-lf-error-100 text-lf-error-700';
  return 'bg-lf-accent-100 text-lf-accent-800';
}

export function scanLabel(status: FileScanStatus): string {
  const normalized = status.toLowerCase();
  if (normalized === 'pending') return 'Scan pending';
  if (normalized === 'clean') return 'Clean';
  if (normalized === 'quarantined') return 'Quarantined';
  return normalized.replace(/_/g, ' ');
}

/** Format bytes as KB/MB for file sizes. */
export function formatBytes(bytes: number | null): string {
  if (bytes == null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  will: 'Will',
  trust: 'Trust',
  deed: 'Deed',
  life_insurance: 'Life insurance',
  financial_statement: 'Financial statement',
  power_of_attorney: 'Power of attorney',
  advance_directive: 'Advance directive',
  operating_agreement: 'Operating agreement',
  tax_return: 'Tax return',
  other: 'Other document',
};

export function documentTypeLabel(type: string | null): string {
  if (!type) return '';
  return DOCUMENT_TYPE_LABELS[type] ?? type.replace(/_/g, ' ');
}

export function ScanBadge({ status }: { status: FileScanStatus }): React.JSX.Element {
  return (
    <span
      className={cn(
        'inline-block rounded-full px-2.5 py-1 text-xs font-medium',
        scanBadgeClass(status),
      )}
    >
      {scanLabel(status)}
    </span>
  );
}

export function DocumentTypeBadge({ type }: { type: string }): React.JSX.Element {
  return (
    <span className="inline-block rounded-full bg-lf-primary-100 px-2.5 py-1 text-xs font-medium text-lf-primary-800">
      {documentTypeLabel(type)}
    </span>
  );
}

function KeyFactsList({ facts }: { facts: FileKeyFact[] }): React.JSX.Element | null {
  if (facts.length === 0) return null;
  return (
    <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
      {facts.map((fact, index) => (
        <div key={`${fact.label}-${index}`} className="flex gap-2 text-xs">
          <dt className="shrink-0 font-medium text-lf-text-muted">{fact.label}:</dt>
          <dd className="min-w-0 truncate text-lf-text" title={fact.value}>
            {fact.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function TagList({ tags }: { tags: string[] }): React.JSX.Element | null {
  if (tags.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {tags.map((tag) => (
        <span
          key={tag}
          className="inline-block rounded-full border border-lf-border bg-lf-canvas-subtle px-2 py-0.5 text-xs text-lf-text-muted"
        >
          {tag}
        </span>
      ))}
    </div>
  );
}

interface FileRowProps {
  file: MatterFile;
  downloadingId: string | null;
  analyzingId: string | null;
  analyzeError: string | null;
  analyzeErrorId: string | null;
  onDownload: (file: MatterFile) => void;
  onAnalyze: (file: MatterFile) => void;
  onAddTag: (file: MatterFile, tag: string) => void;
  allowAnalyze: boolean;
}

function FileRow({
  file,
  downloadingId,
  analyzingId,
  analyzeError,
  analyzeErrorId,
  onDownload,
  onAnalyze,
  onAddTag,
  allowAnalyze,
}: FileRowProps): React.JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const [tagInput, setTagInput] = useState('');
  const [addingTag, setAddingTag] = useState(false);
  const [tagError, setTagError] = useState<string | null>(null);
  const analyzed = file.document_type != null;
  const canAnalyze =
    allowAnalyze && !analyzed && file.extraction_status === 'done' && analyzingId !== file.id;
  const showDetails = analyzed || file.tags.length > 0;

  const handleAddTag = async (event: React.FormEvent) => {
    event.preventDefault();
    const tag = tagInput.trim();
    if (!tag || addingTag) return;
    setAddingTag(true);
    setTagError(null);
    try {
      await onAddTag(file, tag);
      setTagInput('');
    } catch (err: unknown) {
      // trackSave stays silent on 4xx (Stage F F3), so this inline error is
      // the only surface a failed tag add gets — it must catch every failure.
      setTagError(
        err instanceof Error ? `Could not add tag: ${err.message}` : 'Could not add tag.',
      );
    } finally {
      setAddingTag(false);
    }
  };

  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lf-control bg-lf-primary-50 text-lf-primary-800">
          <FileText className="size-4" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-lf-text">{file.original_filename}</p>
          <p className="text-xs text-lf-text-subtle">
            {formatBytes(file.size_bytes)} · uploaded {formatDateTime(file.created_at)}
          </p>
        </div>
        {analyzed && <DocumentTypeBadge type={file.document_type as string} />}
        <ScanBadge status={file.scan_status} />
        {canAnalyze && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onAnalyze(file)}
            title="Extract the document type, key facts, and tags with AI"
          >
            <Sparkles className="size-4" aria-hidden="true" />
            Analyze
          </Button>
        )}
        {analyzingId === file.id && (
          <span className="text-xs text-lf-text-muted" role="status">
            Analyzing…
          </span>
        )}
        {showDetails && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-label={expanded ? 'Hide analysis details' : 'Show analysis details'}
          >
            <ChevronDown
              className={cn('size-4 transition-transform', expanded && 'rotate-180')}
              aria-hidden="true"
            />
          </Button>
        )}
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={downloadingId === file.id || file.scan_status === 'quarantined'}
          onClick={() => onDownload(file)}
          title={
            file.scan_status === 'quarantined'
              ? 'This file was quarantined and cannot be downloaded.'
              : undefined
          }
        >
          <Download className="size-4" aria-hidden="true" />
          {downloadingId === file.id ? 'Preparing…' : 'Download'}
        </Button>
      </div>

      {analyzeError != null && analyzeErrorId === file.id && (
        <p role="alert" className="mt-2 text-xs text-lf-error-700">
          {analyzeError}
        </p>
      )}

      {file.extraction_status === 'unavailable' && (
        <p className="mt-2 text-xs text-lf-text-subtle">
          Text extraction isn’t available for images — analysis needs a PDF or Word document.
        </p>
      )}

      {file.extraction_status === 'unreadable_scan' && (
        <p className="mt-2 text-xs text-lf-text-subtle">
          We couldn’t read this file — it looks like a scanned image with no readable text, and
          vault uploads can’t be scan-recognized. Analysis isn’t available for it; try a
          text-based PDF or Word document instead.
        </p>
      )}

      {showDetails && expanded && (
        <div className="mt-3 rounded-lf-control border border-lf-border bg-lf-canvas-subtle p-3">
          {file.ai_summary && <p className="text-sm text-lf-text">{file.ai_summary}</p>}
          <KeyFactsList facts={file.key_facts} />
          <TagList tags={file.tags} />
          {allowAnalyze && (
            <form onSubmit={handleAddTag} className="mt-2 flex items-center gap-2">
              <input
                type="text"
                value={tagInput}
                onChange={(event) => setTagInput(event.target.value)}
                placeholder="Add a tag…"
                aria-label="Add a tag"
                maxLength={40}
                className="h-8 w-40 rounded-lf-control border border-lf-border bg-lf-canvas-elevated px-2.5 text-xs text-lf-text placeholder:text-lf-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lf-primary-500"
              />
              <Button type="submit" variant="ghost" size="sm" disabled={addingTag || !tagInput.trim()}>
                <Plus className="size-3.5" aria-hidden="true" />
                {addingTag ? 'Adding…' : 'Add'}
              </Button>
            </form>
          )}
          {tagError != null && (
            <p role="alert" className="mt-2 text-xs text-lf-error-700">
              {tagError}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

interface FileListProps {
  files: MatterFile[];
  downloadingId: string | null;
  onDownload: (file: MatterFile) => void;
  downloadError?: string | null;
  /** Called with the refreshed file after analysis completes. */
  onAnalyzed?: (file: MatterFile) => void;
  /** Show the Analyze action (family); firm view is read-only. */
  allowAnalyze?: boolean;
}

/** Merge an analyze response into the file shape for immediate UI refresh. */
function analysisResultToFile(
  analysis: {
    document_type: string;
    summary: string;
    key_facts: FileKeyFact[];
    suggested_tags: string[];
    confidence: number;
    analyzed_at: string;
  },
  file: MatterFile,
): Partial<MatterFile> {
  const tags = [...file.tags];
  for (const tag of analysis.suggested_tags) {
    if (!tags.includes(tag)) tags.push(tag);
  }
  return {
    document_type: analysis.document_type,
    ai_summary: analysis.summary,
    key_facts: analysis.key_facts,
    tags,
    analysis_confidence: analysis.confidence,
    analyzed_at: analysis.analyzed_at,
  };
}

/** Read-only list of matter files with scan badges, AI analysis, and downloads. */
export function FileList({
  files,
  downloadingId,
  onDownload,
  downloadError,
  onAnalyzed,
  allowAnalyze = false,
}: FileListProps): React.JSX.Element {
  const [analyzingId, setAnalyzingId] = useState<string | null>(null);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [analyzeErrorId, setAnalyzeErrorId] = useState<string | null>(null);

  const handleAnalyze = async (file: MatterFile) => {
    setAnalyzingId(file.id);
    setAnalyzeError(null);
    setAnalyzeErrorId(null);
    try {
      const analysis = await api.analyzeFile(file.id);
      onAnalyzed?.({ ...file, ...analysisResultToFile(analysis, file) });
    } catch (err: unknown) {
      setAnalyzeError(
        err instanceof Error ? `Could not analyze: ${err.message}` : 'Could not analyze.',
      );
      setAnalyzeErrorId(file.id);
    } finally {
      setAnalyzingId(null);
    }
  };

  const handleAddTag = async (file: MatterFile, tag: string) => {
    const updated = await api.addFileTags(file.id, [tag]);
    onAnalyzed?.(updated);
  };

  if (files.length === 0) {
    return (
      <EmptyState
        title="No files yet"
        description="Upload documents for this matter — IDs, deeds, account statements, anything your attorney asked for. Every file is scanned before it is available."
      />
    );
  }

  return (
    <div className="space-y-3">
      {downloadError && (
        <p role="alert" className="text-sm text-lf-error-700">
          {downloadError}
        </p>
      )}
      <ul className="divide-y divide-lf-border rounded-lf-card border border-lf-border">
        {files.map((file) => (
          <FileRow
            key={file.id}
            file={file}
            downloadingId={downloadingId}
            analyzingId={analyzingId}
            analyzeError={analyzeError}
            analyzeErrorId={analyzeErrorId}
            onDownload={onDownload}
            onAnalyze={handleAnalyze}
            onAddTag={handleAddTag}
            allowAnalyze={allowAnalyze}
          />
        ))}
      </ul>
    </div>
  );
}
