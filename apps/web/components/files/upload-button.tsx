'use client';

import { useRef, useState } from 'react';
import { Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { api, type MatterFile } from '@/lib/api-client';

/** Client-side upload allow-list: common document and image types. */
export const ALLOWED_TYPES = [
  'application/pdf',
  'image/jpeg',
  'image/png',
  'image/webp',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
] as const;

/** Client-side upload cap: 25 MB. */
export const MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024;

export function validateFile(file: File): string | null {
  if (!ALLOWED_TYPES.includes(file.type as (typeof ALLOWED_TYPES)[number])) {
    return `“${file.name}” is not an accepted type. Upload a PDF, Word document, or image (JPEG, PNG, WebP).`;
  }
  if (file.size > MAX_FILE_SIZE_BYTES) {
    return `“${file.name}” is too large. The limit is 25 MB.`;
  }
  if (file.size === 0) {
    return `“${file.name}” appears to be empty.`;
  }
  return null;
}

interface UploadButtonProps {
  matterId: string;
  onUploaded: (file: MatterFile) => void;
}

/**
 * File upload button: validates type/size client-side, then runs the
 * intent → browser PUT → complete flow and reports the finished file.
 */
export function UploadButton({ matterId, onUploaded }: UploadButtonProps): React.JSX.Element {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFiles = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file || uploading) return;
    const validationError = validateFile(file);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    setUploading(true);
    try {
      const intent = await api.requestIntent(matterId, {
        filename: file.name,
        mime_type: file.type,
        size_bytes: file.size,
        purpose: 'document',
      });
      // The API accepts the bytes as multipart on the complete endpoint.
      const completed = await api.completeUpload(intent.upload_id, file);
      onUploaded(completed);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Could not upload the file.');
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={uploading}
          size="sm"
        >
          <Upload className="size-4" aria-hidden="true" />
          {uploading ? 'Uploading…' : 'Upload file'}
        </Button>
        <p className="text-xs text-lf-text-subtle">
          PDF, Word, JPEG, PNG, or WebP · up to 25 MB · scanned after upload.
        </p>
      </div>
      <input
        ref={inputRef}
        type="file"
        className="sr-only"
        aria-label="Choose a file to upload"
        accept={ALLOWED_TYPES.join(',')}
        onChange={(event) => void handleFiles(event.target.files)}
      />
      {error && (
        <p role="alert" className="text-sm text-lf-error-700">
          {error}
        </p>
      )}
    </div>
  );
}
