/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  createFamilyApi,
  createFirmApi,
  fileDownloadUrl,
  type AiAskResponse,
  type MatterFile,
} from '../lib/api-client';

interface RecordedRequest {
  url: string;
  method: string;
  body: unknown;
  headers: Record<string, string>;
}

const requests: RecordedRequest[] = [];

function mockResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    headers: { get: () => null },
    blob: () => Promise.resolve(new Blob(['demo'], { type: 'application/pdf' })),
  } as unknown as Response;
}

function respondWith(payload: unknown, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      let body: unknown;
      if (init?.body instanceof FormData) {
        body = '[FormData]';
      } else if (init?.body) {
        body = JSON.parse(init.body as string);
      }
      requests.push({
        url,
        method: init?.method ?? 'GET',
        body,
        headers: (init?.headers ?? {}) as Record<string, string>,
      });
      return mockResponse(payload, status);
    }),
  );
}

afterEach(() => {
  requests.length = 0;
  vi.unstubAllGlobals();
});

const sampleFile: MatterFile = {
  id: 'file-1',
  firm_id: 'firm-1',
  household_id: 'household-1',
  matter_id: 'matter-1',
  uploader_user_id: 'user-1',
  original_filename: 'deed.pdf',
  stored_path: 'client-uploads/file-1.pdf',
  mime_type: 'application/pdf',
  size_bytes: 102400,
  scan_status: 'clean',
  scan_detail: null,
  extracted_text: 'GENERAL WARRANTY DEED',
  extraction_status: 'done',
  document_type: 'deed',
  ai_summary: 'A property deed.',
  key_facts: [{ label: 'Grantor', value: 'Robert Smith' }],
  tags: ['deed'],
  analysis_confidence: 0.9,
  analyzed_at: '2026-09-27T12:05:00Z',
  created_at: '2026-09-27T12:00:00Z',
  updated_at: '2026-09-27T12:05:00Z',
};

const askResponse: AiAskResponse = {
  answer: 'Probate is the court process for settling an estate.',
  citations: [{ pack: 'ca-probate-basics', section: 'what-is-probate' }],
  refused: false,
  safe_message: null,
};

describe('Family API — files (Slice 4)', () => {
  const family = createFamilyApi();

  it('lists matter files', async () => {
    respondWith([sampleFile]);
    const files = await family.listFiles('matter-1');
    expect(files).toHaveLength(1);
    expect(requests[0]).toMatchObject({ url: '/v1/matters/matter-1/files', method: 'GET' });
  });

  it('requests an upload intent and completes the upload', async () => {
    respondWith({ upload_id: 'upl-1', stored_path: 'client-uploads/upl-1.pdf', status: 'pending' });
    const intent = await family.requestIntent('matter-1', {
      filename: 'deed.pdf',
      mime_type: 'application/pdf',
      size_bytes: 102400,
      purpose: 'document',
    });
    expect(intent.upload_id).toBe('upl-1');
    expect(requests[0]).toMatchObject({
      url: '/v1/matters/matter-1/files/intent',
      method: 'POST',
      body: {
        filename: 'deed.pdf',
        mime_type: 'application/pdf',
        size_bytes: 102400,
        purpose: 'document',
      },
    });

    respondWith(sampleFile);
    const file = new File(['bytes'], 'deed.pdf', { type: 'application/pdf' });
    const completed = await family.completeUpload('upl-1', file);
    expect(completed.id).toBe('file-1');
    expect(requests[1]).toMatchObject({
      url: '/v1/files/upl-1/complete',
      method: 'POST',
    });
  });

  it('gets a download token', async () => {
    respondWith({ token: 'tok-1', expires_at: '2026-09-27T12:15:00Z' });
    const token = await family.getDownloadToken('file-1');
    expect(token.token).toBe('tok-1');
    expect(requests[0]).toMatchObject({
      url: '/v1/files/file-1/download-token',
      method: 'POST',
    });
  });

  it('analyzes a file and adds tags', async () => {
    respondWith({
      upload_id: 'file-1',
      document_type: 'deed',
      summary: 'A property deed.',
      key_facts: [{ label: 'Grantor', value: 'Robert Smith' }],
      suggested_tags: ['deed'],
      confidence: 0.9,
      analyzed_at: '2026-09-27T12:05:00Z',
    });
    const analysis = await family.analyzeFile('file-1');
    expect(analysis.document_type).toBe('deed');
    expect(requests[0]).toMatchObject({
      url: '/v1/files/file-1/analyze',
      method: 'POST',
    });

    respondWith({ ...sampleFile, tags: ['deed', 'reviewed'] });
    const updated = await family.addFileTags('file-1', ['reviewed']);
    expect(updated.tags).toContain('reviewed');
    expect(requests[1]).toMatchObject({
      url: '/v1/files/file-1/tags',
      method: 'POST',
      body: { tags: ['reviewed'] },
    });
  });

  it('builds a token download URL', () => {
    expect(fileDownloadUrl('file-1', 'tok-1')).toBe('/v1/files/download?token=tok-1');
  });
});

describe('Family API — AI assistant (Slice 4)', () => {
  const family = createFamilyApi();

  it('asks a question', async () => {
    respondWith(askResponse);
    const response = await family.askAi('matter-1', 'What is probate?');
    expect(response.answer).toContain('Probate');
    expect(response.citations).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      url: '/v1/matters/matter-1/ai/ask',
      method: 'POST',
      body: { question: 'What is probate?' },
    });
  });

  it('returns refused questions with a safe message', async () => {
    respondWith({
      answer: '',
      citations: [],
      refused: true,
      safe_message: 'I can’t answer that — please ask your attorney.',
    });
    const response = await family.askAi('matter-1', 'Should I disinherit my child?');
    expect(response.refused).toBe(true);
    expect(response.safe_message).toContain('ask your attorney');
  });
});

describe('Family API — documents (Slice 4)', () => {
  const family = createFamilyApi();

  it('lists demo documents and gets a download token', async () => {
    respondWith([{ id: 'doc-1', file_name: 'will-demo.pdf', is_demo: true }]);
    const docs = await family.listDraftDocuments('matter-1');
    expect(docs).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      url: '/v1/matters/matter-1/document-versions',
      method: 'GET',
    });
  });
});

describe('Family API — signing and funding (Slice 4)', () => {
  const family = createFamilyApi();

  it('gets the checklist and attests a requirement', async () => {
    respondWith({ matter_id: 'matter-1', requirements: [], attestations: [] });
    await family.getChecklist('matter-1');
    expect(requests[0]).toMatchObject({
      url: '/v1/matters/matter-1/signing-checklist',
      method: 'GET',
    });

    respondWith({ id: 'att-1', requirement_id: 'req-1' });
    await family.attest('checklist-1', {
      requirement_id: 'req-1',
      acknowledgement: 'I have read this requirement.',
    });
    expect(requests[1]).toMatchObject({
      url: '/v1/signing-checklists/checklist-1/attest',
      method: 'POST',
      body: { requirement_id: 'req-1', acknowledgement: 'I have read this requirement.' },
    });
  });

  it('lists and updates funding tasks', async () => {
    respondWith([{ id: 'task-1', status: 'pending' }]);
    const tasks = await family.listFundingTasks('matter-1');
    expect(tasks).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      url: '/v1/matters/matter-1/funding-tasks',
      method: 'GET',
    });

    respondWith({ id: 'task-1', status: 'complete' });
    await family.updateFundingTask('task-1', { status: 'complete', evidence_file_id: 'file-1' });
    expect(requests[1]).toMatchObject({
      url: '/v1/funding-tasks/task-1',
      method: 'PATCH',
      body: { status: 'complete', evidence_file_id: 'file-1' },
    });
  });
});

describe('Family API — vault (Slice 4)', () => {
  const family = createFamilyApi();

  it('lists, creates, and revokes shares', async () => {
    respondWith([]);
    await family.listShares('matter-1');
    expect(requests[0]).toMatchObject({
      url: '/v1/matters/matter-1/vault-shares',
      method: 'GET',
    });

    respondWith({ id: 'share-1', label: 'For Ana', token: 'secret-token' });
    const created = await family.createShare('matter-1', {
      label: 'For Ana',
      expires_at: '2026-10-27T00:00:00Z',
    });
    expect(created.token).toBe('secret-token');
    expect(requests[1]).toMatchObject({
      url: '/v1/matters/matter-1/vault-shares',
      method: 'POST',
      body: { label: 'For Ana', expires_at: '2026-10-27T00:00:00Z' },
    });

    respondWith({ id: 'share-1', revoked_at: '2026-09-27T12:00:00Z' });
    const revoked = await family.revokeShare('share-1');
    expect(revoked.revoked_at).not.toBeNull();
    expect(requests[2]).toMatchObject({
      url: '/v1/vault-shares/share-1/revoke',
      method: 'POST',
    });
  });

  it('accesses a share by token without an Authorization header', async () => {
    const authed = createFamilyApi({ getToken: () => 'jwt-abc' });
    respondWith({ share_label: 'For Ana', redacted: true, documents: [] });
    const summary = await authed.accessByToken('secret-token');
    expect(summary.redacted).toBe(true);
    expect(requests[0]).toMatchObject({ url: '/v1/vault/access', method: 'POST' });
    expect(requests[0]?.headers['Authorization']).toBeUndefined();
  });
});

describe('Firm API (Slice 4)', () => {
  const firm = createFirmApi();

  it('lists matter files and asks the assistant', async () => {
    respondWith([sampleFile]);
    await firm.listFiles('matter-1');
    expect(requests[0]).toMatchObject({ url: '/v1/matters/matter-1/files', method: 'GET' });

    respondWith(askResponse);
    await firm.askAi('matter-1', 'Summarize this matter.');
    expect(requests[1]).toMatchObject({
      url: '/v1/matters/matter-1/ai/ask',
      method: 'POST',
      body: { question: 'Summarize this matter.' },
    });
  });

  it('lists templates, creates a package, and generates a demo blob', async () => {
    respondWith([{ id: 'tpl-1', name: 'Will' }]);
    await firm.listTemplates();
    expect(requests[0]).toMatchObject({
      url: '/v1/document-templates',
      method: 'GET',
    });

    respondWith({ id: 'pkg-1', template_ids: ['tpl-1'] });
    await firm.createPackage('matter-1', { template_ids: ['tpl-1'] });
    expect(requests[1]).toMatchObject({
      url: '/v1/matters/matter-1/document-packages',
      method: 'POST',
      body: { template_ids: ['tpl-1'] },
    });

    respondWith({ package: { id: 'pkg-1' }, versions: [] });
    const demo = await firm.generateDraft('pkg-1', 'pdf');
    expect(demo.versions).toEqual([]);
    expect(requests[2]).toMatchObject({
      url: '/v1/packages/pkg-1/generate-demo',
      method: 'POST',
    });
  });

  it('surfaces production release as a 409 ApiError', async () => {
    respondWith({ code: 'production_not_available', message: 'Not available' }, 409);
    await expect(firm.releaseProduction('pkg-1')).rejects.toMatchObject({ status: 409 });
    expect(requests[0]).toMatchObject({
      url: '/v1/packages/pkg-1/release-production',
      method: 'POST',
    });
  });

  it('gets the signing checklist and funding tasks', async () => {
    respondWith({ requirements: [], attestations: [] });
    await firm.getChecklist('matter-1');
    expect(requests[0]).toMatchObject({
      url: '/v1/matters/matter-1/signing/checklist',
      method: 'GET',
    });

    respondWith([{ id: 'task-1' }]);
    await firm.generateFundingTasks('matter-1');
    expect(requests[1]).toMatchObject({
      url: '/v1/matters/matter-1/funding-tasks/generate',
      method: 'POST',
    });

    respondWith([]);
    await firm.listFundingTasks('matter-1');
    expect(requests[2]).toMatchObject({
      url: '/v1/matters/matter-1/funding-tasks',
      method: 'GET',
    });
  });

  it('releaseProduction maps a 409 body to an ApiError instance', async () => {
    respondWith({ code: 'production_not_available' }, 409);
    const err = await firm.releaseProduction('pkg-1').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
  });
});
