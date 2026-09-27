/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  createFamilyApi,
  createFirmApi,
  type Asset,
  type Liability,
} from '../lib/api-client';

interface RecordedRequest {
  url: string;
  method: string;
  body: unknown;
}

const requests: RecordedRequest[] = [];

function mockResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
  } as Response;
}

function respondWith(payload: unknown, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      requests.push({
        url,
        method: init?.method ?? 'GET',
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      });
      return mockResponse(payload, status);
    }),
  );
}

afterEach(() => {
  requests.length = 0;
  vi.unstubAllGlobals();
});

const sampleAsset: Asset = {
  id: 'asset-1',
  firm_id: 'firm-1',
  household_id: 'household-1',
  matter_id: 'matter-1',
  asset_type: 'real_estate',
  label: 'Maple Street house',
  estimated_value_cents: 45000000,
  ownership: 'joint',
  beneficiary_designation: { primary: [], contingent: [] },
  notes: null,
  created_at: '2026-09-20T12:00:00Z',
  updated_at: '2026-09-21T12:00:00Z',
};

const sampleLiability: Liability = {
  id: 'liability-1',
  firm_id: 'firm-1',
  household_id: 'household-1',
  matter_id: 'matter-1',
  liability_type: 'mortgage',
  label: 'Maple Street mortgage',
  balance_cents: 28000000,
  monthly_payment_cents: 180000,
  notes: null,
  created_at: '2026-09-20T12:00:00Z',
  updated_at: '2026-09-21T12:00:00Z',
};

const assetList = { items: [sampleAsset], page: 1, page_size: 25, total: 1 };
const liabilityList = { items: [sampleLiability], page: 1, page_size: 25, total: 1 };

describe('Family API — assets and liabilities (Slice 3)', () => {
  it('lists household assets as a paginated list', async () => {
    respondWith(assetList);
    const client = createFamilyApi();
    const result = await client.listAssets('household-1');
    expect(result.items).toHaveLength(1);
    expect(result.total).toBe(1);
    expect(requests[0]).toMatchObject({
      url: '/v1/households/household-1/assets?page=1&page_size=25',
      method: 'GET',
    });
  });

  it('creates an asset under the household with the input body', async () => {
    respondWith(sampleAsset, 201);
    const client = createFamilyApi();
    const asset = await client.createAsset('household-1', {
      matter_id: 'matter-1',
      asset_type: 'real_estate',
      label: 'Maple Street house',
      estimated_value_cents: 45000000,
      ownership: 'joint',
    });
    expect(asset.id).toBe('asset-1');
    expect(requests[0]).toMatchObject({
      url: '/v1/households/household-1/assets',
      method: 'POST',
      body: {
        matter_id: 'matter-1',
        asset_type: 'real_estate',
        label: 'Maple Street house',
        estimated_value_cents: 45000000,
        ownership: 'joint',
      },
    });
  });

  it('gets, patches, and deletes an asset by id', async () => {
    const client = createFamilyApi();
    respondWith(sampleAsset);
    await client.getAsset('asset-1');
    expect(requests[0]).toMatchObject({ url: '/v1/assets/asset-1', method: 'GET' });

    requests.length = 0;
    respondWith({ ...sampleAsset, label: 'Renamed' });
    await client.updateAsset('asset-1', { label: 'Renamed' });
    expect(requests[0]).toMatchObject({
      url: '/v1/assets/asset-1',
      method: 'PATCH',
      body: { label: 'Renamed' },
    });

    requests.length = 0;
    respondWith(null, 204);
    await client.deleteAsset('asset-1');
    expect(requests[0]).toMatchObject({ url: '/v1/assets/asset-1', method: 'DELETE' });
  });

  it('replaces an asset beneficiary designation', async () => {
    respondWith(sampleAsset);
    const client = createFamilyApi();
    await client.updateAssetBeneficiaries('asset-1', {
      primary: [{ name: 'Jordan Lee', relationship: 'Spouse' }],
      contingent: [{ name: 'Casey Lee', relationship: 'Child' }],
    });
    expect(requests[0]).toMatchObject({
      url: '/v1/assets/asset-1/beneficiary',
      method: 'PATCH',
      body: {
        primary: [{ name: 'Jordan Lee', relationship: 'Spouse' }],
        contingent: [{ name: 'Casey Lee', relationship: 'Child' }],
      },
    });
  });

  it('evaluates alignment for one asset', async () => {
    const payload = {
      asset_id: 'asset-1',
      flags: [
        {
          id: 'flag-1',
          firm_id: 'firm-1',
          asset_id: 'asset-1',
          flag_type: 'missing_contingent',
          severity: 'review',
          message: 'No contingent beneficiary.',
          resolved_at: null,
          created_at: '2026-09-26T12:00:00Z',
        },
      ],
      flag_counts: { review: 1 },
    };
    respondWith(payload);
    const client = createFamilyApi();
    const result = await client.evaluateAssetAlignment('asset-1');
    expect(result.asset_id).toBe('asset-1');
    expect(result.flags).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      url: '/v1/assets/asset-1/evaluate-alignment',
      method: 'POST',
    });
  });

  it('supports the full liability surface', async () => {
    const client = createFamilyApi();
    respondWith(liabilityList);
    const listed = await client.listLiabilities('household-1');
    expect(listed.items).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      url: '/v1/households/household-1/liabilities?page=1&page_size=25',
      method: 'GET',
    });

    requests.length = 0;
    respondWith(sampleLiability, 201);
    await client.createLiability('household-1', {
      matter_id: 'matter-1',
      liability_type: 'mortgage',
      label: 'Maple Street mortgage',
      balance_cents: 28000000,
    });
    expect(requests[0]).toMatchObject({
      url: '/v1/households/household-1/liabilities',
      method: 'POST',
      body: { liability_type: 'mortgage', label: 'Maple Street mortgage' },
    });

    requests.length = 0;
    respondWith({ ...sampleLiability, balance_cents: 27500000 });
    await client.updateLiability('liability-1', { balance_cents: 27500000 });
    expect(requests[0]).toMatchObject({
      url: '/v1/liabilities/liability-1',
      method: 'PATCH',
      body: { balance_cents: 27500000 },
    });

    requests.length = 0;
    respondWith(null, 204);
    await client.deleteLiability('liability-1');
    expect(requests[0]).toMatchObject({ url: '/v1/liabilities/liability-1', method: 'DELETE' });
  });
});

describe('Firm API (Slice 3)', () => {
  it('lists matters with search, status, and jurisdiction filters plus pagination', async () => {
    respondWith({ items: [], page: 2, page_size: 20, total: 0 });
    const client = createFirmApi();
    await client.listMatters({
      q: 'smith',
      status: 'attorney_review',
      jurisdictionCode: 'US-CA',
      page: 2,
      pageSize: 20,
    });
    const [request] = requests;
    expect(request?.method).toBe('GET');
    const params = new URLSearchParams(request?.url.split('?')[1] ?? '');
    expect(params.get('q')).toBe('smith');
    expect(params.get('status')).toBe('attorney_review');
    expect(params.get('jurisdiction_code')).toBe('US-CA');
    expect(params.get('page')).toBe('2');
    expect(params.get('page_size')).toBe('20');
  });

  it('omits empty filter values', async () => {
    respondWith({ items: [], page: 1, page_size: 20, total: 0 });
    const client = createFirmApi();
    await client.listMatters({});
    const params = new URLSearchParams(requests[0]?.url.split('?')[1] ?? '');
    expect(params.get('q')).toBeNull();
    expect(params.get('status')).toBeNull();
    expect(params.get('jurisdiction_code')).toBeNull();
    expect(params.get('page')).toBe('1');
  });

  it('generates, lists, and fetches review packets', async () => {
    const packet = {
      id: 'packet-1',
      firm_id: 'firm-1',
      matter_id: 'matter-1',
      version: 2,
      created_by_user_id: 'user-1',
      snapshot: {},
      created_at: '2026-09-26T12:00:00Z',
    };
    const client = createFirmApi();
    respondWith(packet, 201);
    await client.generateReviewPacket('matter-1');
    expect(requests[0]).toMatchObject({
      url: '/v1/matters/matter-1/review-packets',
      method: 'POST',
    });

    requests.length = 0;
    respondWith([{ ...packet, snapshot: undefined }]);
    await client.listReviewPackets('matter-1');
    expect(requests[0]).toMatchObject({
      url: '/v1/matters/matter-1/review-packets',
      method: 'GET',
    });

    requests.length = 0;
    respondWith(packet);
    const detail = await client.getReviewPacket('matter-1', 'packet-1');
    expect(detail.snapshot).toEqual({});
    expect(requests[0]).toMatchObject({
      url: '/v1/matters/matter-1/review-packets/packet-1',
      method: 'GET',
    });
  });

  it('creates comments', async () => {
    respondWith({ id: 'comment-1', body: 'Looks good', created_at: '2026-09-26T12:00:00Z' }, 201);
    const client = createFirmApi();
    await client.createComment('matter-1', { body: 'Looks good' });
    expect(requests[0]).toMatchObject({
      url: '/v1/matters/matter-1/comments',
      method: 'POST',
      body: { body: 'Looks good' },
    });
  });

  it('creates clarifications and posts responses to a clarification id', async () => {
    const clarification = {
      id: 'clar-1',
      firm_id: 'firm-1',
      matter_id: 'matter-1',
      question: 'Who gets the coin collection?',
      status: 'open',
      created_by_user_id: 'user-1',
      answered_by_user_id: null,
      answered_at: null,
      response: null,
      created_at: '2026-09-26T12:00:00Z',
      updated_at: '2026-09-26T12:00:00Z',
    };
    const client = createFirmApi();
    respondWith(clarification, 201);
    await client.createClarification('matter-1', { question: 'Who gets the coin collection?' });
    expect(requests[0]).toMatchObject({
      url: '/v1/matters/matter-1/clarifications',
      method: 'POST',
      body: { question: 'Who gets the coin collection?' },
    });

    requests.length = 0;
    respondWith({ ...clarification, response: 'My sister', status: 'answered' });
    await client.respondClarification('clar-1', { response: 'My sister' });
    expect(requests[0]).toMatchObject({
      url: '/v1/clarifications/clar-1/respond',
      method: 'POST',
      body: { response: 'My sister' },
    });
  });

  it('creates and decides gates by gate id', async () => {
    const gate = {
      id: 'gate-1',
      firm_id: 'firm-1',
      matter_id: 'matter-1',
      gate_type: 'intake_approval',
      status: 'pending',
      decided_by_user_id: null,
      decided_at: null,
      notes: null,
      created_at: '2026-09-26T12:00:00Z',
      updated_at: '2026-09-26T12:00:00Z',
    };
    const client = createFirmApi();
    respondWith(gate, 201);
    await client.createGate('matter-1', { gate_type: 'intake_approval' });
    expect(requests[0]).toMatchObject({
      url: '/v1/matters/matter-1/gates',
      method: 'POST',
      body: { gate_type: 'intake_approval' },
    });

    requests.length = 0;
    respondWith({ ...gate, status: 'approved', notes: 'All set.' });
    const decided = await client.decideGate('gate-1', { decision: 'approved', notes: 'All set.' });
    expect(decided.status).toBe('approved');
    expect(requests[0]).toMatchObject({
      url: '/v1/gates/gate-1/decide',
      method: 'POST',
      body: { decision: 'approved', notes: 'All set.' },
    });
  });

  it('throws ApiError with the server message on failure', async () => {
    respondWith({ code: 'forbidden', message: 'No access.' }, 403);
    const client = createFirmApi();
    await expect(client.listGates('matter-1')).rejects.toMatchObject({
      message: 'No access.',
    });
    try {
      await client.listGates('matter-1');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).isPermissionDenied).toBe(true);
    }
  });
});
