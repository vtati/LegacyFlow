'use client';

import { Download, FileText } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/family/page-state';
import type { FileScanStatus, MatterFile } from '@/lib/api-client';
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
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface FileListProps {
  files: MatterFile[];
  downloadingId: string | null;
  onDownload: (file: MatterFile) => void;
  downloadError?: string | null;
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

/** Read-only list of matter files with scan badges and download actions. */
export function FileList({
  files,
  downloadingId,
  onDownload,
  downloadError,
}: FileListProps): React.JSX.Element {
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
          <li key={file.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-lf-control bg-lf-primary-50 text-lf-primary-800">
              <FileText className="size-4" aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-lf-text">{file.filename}</p>
              <p className="text-xs text-lf-text-subtle">
                {formatBytes(file.size_bytes)} · uploaded {formatDateTime(file.created_at)}
              </p>
            </div>
            <ScanBadge status={file.scan_status} />
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
          </li>
        ))}
      </ul>
    </div>
  );
}
