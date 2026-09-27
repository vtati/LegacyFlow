/**
 * Typed client for the LegacyFlow family API (Tracks B/C contract).
 *
 * Token-agnostic by design: the JWT comes from the `getToken` callback so the
 * client never reaches into auth internals. INTEGRATION POINT: once Tracks B/C
 * finalize how the browser obtains the API JWT (e.g. via the auth adapter or a
 * server action), wire it in as the `getToken` callback on the shared `api`
 * export below. Until then every request is sent unauthenticated.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly details?: unknown;

  constructor(status: number, message: string, opts?: { code?: string; details?: unknown }) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = opts?.code;
    this.details = opts?.details;
  }

  get isPermissionDenied(): boolean {
    return this.status === 403;
  }
}

export interface ApiClientOptions {
  /** API origin; defaults to same-origin. The `/v1` prefix is added automatically. */
  baseUrl?: string;
  /** Returns the JWT for the `Authorization` header, or null when unauthenticated. */
  getToken?: () => string | null | Promise<string | null>;
}

interface ErrorBody {
  code?: string;
  message?: string;
  error?: string;
  detail?: string;
}

function readErrorMessage(status: number, body: unknown): { message: string; code?: string } {
  if (body && typeof body === 'object') {
    const parsed = body as ErrorBody;
    const message = parsed.message ?? parsed.error ?? parsed.detail ?? parsed.code;
    if (typeof message === 'string' && message.length > 0) {
      return { message, code: parsed.code };
    }
  }
  return { message: `Request failed with status ${status}.` };
}

export async function apiFetch<T>(
  path: string,
  options: ApiClientOptions & {
    method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
    body?: unknown;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  const { baseUrl = '', getToken, method = 'GET', body, signal } = options;
  const headers: Record<string, string> = {};
  const token = await getToken?.();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  const response = await fetch(`${baseUrl}/v1${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    let parsed: unknown = null;
    try {
      parsed = await response.json();
    } catch {
      parsed = null;
    }
    const { message, code } = readErrorMessage(response.status, parsed);
    throw new ApiError(response.status, message, { code, details: parsed });
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/* ------------------------------------------------------------------ types */

export interface HouseholdRef {
  id: string;
  name: string;
}

export type MatterStatus = string;

export interface MatterSummary {
  id: string;
  title: string;
  jurisdiction_code: string;
  status: MatterStatus;
  completeness_pct: number;
  household: HouseholdRef;
  created_at: string;
  updated_at: string;
}

export interface MattersListResponse {
  items: MatterSummary[];
  page: number;
  page_size: number;
  total: number;
}

/** Generic paginated list envelope (Slice 3 list endpoints). */
export interface PaginatedList<T> {
  items: T[];
  page: number;
  page_size: number;
  total: number;
}

export interface MatterDetail {
  matter: MatterSummary;
  household: HouseholdRef;
  progress: {
    completeness_pct: number;
    status: MatterStatus;
  };
}

export type QuestionType =
  'text' | 'date' | 'single_select' | 'multi_select' | 'boolean' | 'person' | 'notes';

export interface QuestionOption {
  value: string;
  label: string;
}

export interface Question {
  key: string;
  type: QuestionType;
  label: string;
  help?: string | null;
  required: boolean;
  options?: QuestionOption[];
  maps_to?: string | null;
}

export interface QuestionSection {
  id: string;
  title: string;
  questions: Question[];
}

export interface QuestionSet {
  question_set: { id: string; title: string };
  sections: QuestionSection[];
}

export interface QuestionSetsResponse {
  jurisdiction_code: string;
  pack_version: string;
  question_sets: {
    household: QuestionSet;
    guardians: QuestionSet;
    assets: QuestionSet;
  };
}

export type AnswerValue = string | string[] | boolean | null;
export type AnswerMap = Record<string, AnswerValue>;

export interface AnswersResponse {
  answers: AnswerMap;
  updated_at: string;
}

export interface PutAnswersResponse {
  saved: boolean;
  progress: SetProgress;
}

export interface SetProgress {
  total_questions: number;
  answered: number;
  required_total: number;
  required_answered: number;
  percent_complete: number;
}

export interface IntakeProgressResponse {
  sets: Record<string, SetProgress>;
  overall: SetProgress;
}

export type RuleSeverity = 'blocking' | 'review' | 'info' | string;

export interface TriggeredRule {
  rule_id: string;
  severity: RuleSeverity;
  outcome: string;
  user_message: string;
}

export interface EvaluateResponse {
  triggered: TriggeredRule[];
  has_blocking: boolean;
}

export interface Person {
  id: string;
  full_name: string;
  date_of_birth: string | null;
  email: string | null;
  kind: string;
}

export interface PersonInput {
  full_name: string;
  date_of_birth: string | null;
  email?: string | null;
  kind: string;
}

export interface HouseholdMember {
  id: string;
  person: Person | string;
  relationship_role: string;
  access_status: string;
}

export interface HouseholdResponse {
  household: HouseholdRef;
  members: HouseholdMember[];
  persons: Person[];
  relationships: unknown;
}

export type NominationType =
  'guardian' | 'trustee' | 'executor' | 'financial_agent' | 'healthcare_agent';

export interface Nomination {
  id: string;
  nomination_type: string;
  person_id: string;
  priority: number;
  notes: string | null;
  status: string;
}

export interface NominationInput {
  nomination_type: NominationType;
  person_id: string;
  priority: number;
  notes?: string | null;
}

/* ------------------------------------------------------------------- assets */

export type AssetType = string;
export type OwnershipType = string;

export interface BeneficiaryEntry {
  name: string;
  relationship: string | null;
}

export interface BeneficiaryDesignation {
  primary: BeneficiaryEntry[];
  contingent: BeneficiaryEntry[];
}

export interface Asset {
  id: string;
  firm_id: string;
  household_id: string;
  matter_id: string | null;
  asset_type: AssetType;
  label: string;
  estimated_value_cents: number | null;
  ownership: OwnershipType | null;
  beneficiary_designation: BeneficiaryDesignation;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface AssetInput {
  matter_id?: string | null;
  asset_type: string;
  label: string;
  estimated_value_cents: number;
  ownership?: string | null;
  beneficiary_designation?: BeneficiaryDesignation | null;
  notes?: string | null;
}

export interface AssetPatchInput {
  matter_id?: string | null;
  asset_type?: string;
  label?: string;
  estimated_value_cents?: number;
  ownership?: string | null;
  notes?: string | null;
}

export interface AssetFlag {
  id: string;
  firm_id: string;
  asset_id: string;
  flag_type: string;
  severity: RuleSeverity;
  message: string;
  resolved_at: string | null;
  created_at: string;
}

export interface AlignmentEvaluationResponse {
  asset_id: string;
  flags: AssetFlag[];
  flag_counts: Record<string, number>;
}

/* -------------------------------------------------------------- liabilities */

export type LiabilityType = string;

export interface Liability {
  id: string;
  firm_id: string;
  household_id: string;
  matter_id: string | null;
  liability_type: LiabilityType;
  label: string;
  balance_cents: number;
  monthly_payment_cents: number | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface LiabilityInput {
  matter_id?: string | null;
  liability_type: string;
  label: string;
  balance_cents: number;
  monthly_payment_cents?: number | null;
  notes?: string | null;
}

export interface LiabilityPatchInput {
  matter_id?: string | null;
  liability_type?: string;
  label?: string;
  balance_cents?: number;
  monthly_payment_cents?: number | null;
  notes?: string | null;
}

/* ------------------------------------------------------------- review packet */

export interface ReviewPacketSummary {
  id: string;
  firm_id: string;
  matter_id: string;
  version: number;
  created_by_user_id: string;
  created_at: string;
}

export interface ReviewPacketDetail extends ReviewPacketSummary {
  /** Rendered snapshot content for the attorney viewer. */
  snapshot: Record<string, unknown>;
}

export interface ReviewPacketGenerateInput {
  notes?: string | null;
}

/* ----------------------------------------------------------------- comments */

export interface MatterComment {
  id: string;
  firm_id: string;
  matter_id: string;
  author_user_id: string;
  body: string;
  created_at: string;
}

export interface CommentInput {
  body: string;
}

/* ------------------------------------------------------------ clarifications */

export type ClarificationStatus = 'open' | 'answered';

export interface Clarification {
  id: string;
  firm_id: string;
  matter_id: string;
  question: string;
  status: ClarificationStatus;
  created_by_user_id: string;
  answered_by_user_id: string | null;
  answered_at: string | null;
  response: string | null;
  created_at: string;
  updated_at: string;
}

export interface ClarificationInput {
  question: string;
}

export interface ClarificationRespondInput {
  response: string;
}

/* -------------------------------------------------------------------- gates */

export type GateType = string;
export type GateDecision = 'approved' | 'rejected';
export type GateStatus = 'pending' | 'approved' | 'rejected';

export interface Gate {
  id: string;
  firm_id: string;
  matter_id: string;
  gate_type: GateType;
  status: GateStatus;
  decided_by_user_id: string | null;
  decided_at: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface GateInput {
  gate_type: string;
  notes?: string | null;
}

export interface GateDecisionInput {
  decision: GateDecision;
  notes?: string | null;
}

/* ------------------------------------------------------------------ client */

export interface FamilyApi {
  listMatters: (page?: number, pageSize?: number) => Promise<MattersListResponse>;
  getMatter: (matterId: string) => Promise<MatterDetail>;
  getQuestionSets: (matterId: string) => Promise<QuestionSetsResponse>;
  getAnswers: (matterId: string) => Promise<AnswersResponse>;
  putAnswers: (matterId: string, answers: AnswerMap) => Promise<PutAnswersResponse>;
  getIntakeProgress: (matterId: string) => Promise<IntakeProgressResponse>;
  evaluateIntake: (matterId: string) => Promise<EvaluateResponse>;
  submitMatter: (matterId: string) => Promise<MatterDetail>;
  getHousehold: (matterId: string) => Promise<HouseholdResponse>;
  createPerson: (matterId: string, input: PersonInput) => Promise<Person>;
  updatePerson: (
    matterId: string,
    personId: string,
    input: Partial<PersonInput>,
  ) => Promise<Person>;
  listNominations: (matterId: string, type: NominationType) => Promise<Nomination[]>;
  createNomination: (matterId: string, input: NominationInput) => Promise<Nomination>;
  deleteNomination: (matterId: string, nominationId: string) => Promise<void>;
  /* assets (household-scoped; pass the matter's household id) */
  listAssets: (
    householdId: string,
    page?: number,
    pageSize?: number,
  ) => Promise<PaginatedList<Asset>>;
  createAsset: (householdId: string, input: AssetInput) => Promise<Asset>;
  getAsset: (assetId: string) => Promise<Asset>;
  updateAsset: (assetId: string, input: AssetPatchInput) => Promise<Asset>;
  deleteAsset: (assetId: string) => Promise<void>;
  updateAssetBeneficiaries: (
    assetId: string,
    designation: BeneficiaryDesignation,
  ) => Promise<Asset>;
  evaluateAssetAlignment: (assetId: string) => Promise<AlignmentEvaluationResponse>;
  /* liabilities (household-scoped; pass the matter's household id) */
  listLiabilities: (
    householdId: string,
    page?: number,
    pageSize?: number,
  ) => Promise<PaginatedList<Liability>>;
  createLiability: (householdId: string, input: LiabilityInput) => Promise<Liability>;
  getLiability: (liabilityId: string) => Promise<Liability>;
  updateLiability: (liabilityId: string, input: LiabilityPatchInput) => Promise<Liability>;
  deleteLiability: (liabilityId: string) => Promise<void>;
}

export function createFamilyApi(options: ApiClientOptions = {}): FamilyApi {
  const call = <T>(
    path: string,
    init?: {
      method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
      body?: unknown;
      signal?: AbortSignal;
    },
  ): Promise<T> => apiFetch<T>(path, { ...options, ...init });

  return {
    listMatters: (page = 1, pageSize = 20) =>
      call<MattersListResponse>(`/matters?page=${page}&page_size=${pageSize}`),
    getMatter: (matterId) => call<MatterDetail>(`/matters/${matterId}`),
    getQuestionSets: (matterId) =>
      call<QuestionSetsResponse>(`/matters/${matterId}/intake/question-sets`),
    getAnswers: (matterId) => call<AnswersResponse>(`/matters/${matterId}/intake/answers`),
    putAnswers: (matterId, answers) =>
      call<PutAnswersResponse>(`/matters/${matterId}/intake/answers`, {
        method: 'PUT',
        body: { answers },
      }),
    getIntakeProgress: (matterId) =>
      call<IntakeProgressResponse>(`/matters/${matterId}/intake/progress`),
    evaluateIntake: (matterId) =>
      call<EvaluateResponse>(`/matters/${matterId}/intake/evaluate`, { method: 'POST' }),
    submitMatter: (matterId) =>
      call<MatterDetail>(`/matters/${matterId}/submit`, { method: 'POST' }),
    getHousehold: (matterId) => call<HouseholdResponse>(`/matters/${matterId}/household`),
    createPerson: (matterId, input) =>
      call<Person>(`/matters/${matterId}/persons`, { method: 'POST', body: input }),
    updatePerson: (matterId, personId, input) =>
      call<Person>(`/matters/${matterId}/persons/${personId}`, { method: 'PATCH', body: input }),
    listNominations: (matterId, type) =>
      call<Nomination[]>(`/matters/${matterId}/nominations?type=${type}`),
    createNomination: (matterId, input) =>
      call<Nomination>(`/matters/${matterId}/nominations`, { method: 'POST', body: input }),
    deleteNomination: (matterId, nominationId) =>
      call<void>(`/matters/${matterId}/nominations/${nominationId}`, { method: 'DELETE' }),
    /* assets */
    listAssets: (householdId, page = 1, pageSize = 25) =>
      call<PaginatedList<Asset>>(
        `/households/${householdId}/assets?page=${page}&page_size=${pageSize}`,
      ),
    createAsset: (householdId, input) =>
      call<Asset>(`/households/${householdId}/assets`, { method: 'POST', body: input }),
    getAsset: (assetId) => call<Asset>(`/assets/${assetId}`),
    updateAsset: (assetId, input) =>
      call<Asset>(`/assets/${assetId}`, { method: 'PATCH', body: input }),
    deleteAsset: (assetId) => call<void>(`/assets/${assetId}`, { method: 'DELETE' }),
    updateAssetBeneficiaries: (assetId, designation) =>
      call<Asset>(`/assets/${assetId}/beneficiary`, {
        method: 'PATCH',
        body: designation,
      }),
    evaluateAssetAlignment: (assetId) =>
      call<AlignmentEvaluationResponse>(`/assets/${assetId}/evaluate-alignment`, {
        method: 'POST',
      }),
    /* liabilities */
    listLiabilities: (householdId, page = 1, pageSize = 25) =>
      call<PaginatedList<Liability>>(
        `/households/${householdId}/liabilities?page=${page}&page_size=${pageSize}`,
      ),
    createLiability: (householdId, input) =>
      call<Liability>(`/households/${householdId}/liabilities`, {
        method: 'POST',
        body: input,
      }),
    getLiability: (liabilityId) => call<Liability>(`/liabilities/${liabilityId}`),
    updateLiability: (liabilityId, input) =>
      call<Liability>(`/liabilities/${liabilityId}`, { method: 'PATCH', body: input }),
    deleteLiability: (liabilityId) =>
      call<void>(`/liabilities/${liabilityId}`, { method: 'DELETE' }),
  };
}

/**
 * Shared browser client. Uses a no-op `getToken` until the API auth integration
 * lands (see note at the top of this file).
 */
export const api: FamilyApi = createFamilyApi();

/* ------------------------------------------------------------------ firm api */

export interface FirmMatterFilters {
  q?: string;
  status?: string;
  jurisdictionCode?: string;
  page?: number;
  pageSize?: number;
}

export interface FirmApi {
  listMatters: (filters?: FirmMatterFilters) => Promise<MattersListResponse>;
  getMatter: (matterId: string) => Promise<MatterDetail>;
  generateReviewPacket: (
    matterId: string,
    input?: ReviewPacketGenerateInput,
  ) => Promise<ReviewPacketDetail>;
  listReviewPackets: (matterId: string) => Promise<ReviewPacketSummary[]>;
  getReviewPacket: (matterId: string, packetId: string) => Promise<ReviewPacketDetail>;
  listComments: (matterId: string) => Promise<MatterComment[]>;
  createComment: (matterId: string, input: CommentInput) => Promise<MatterComment>;
  listClarifications: (matterId: string) => Promise<Clarification[]>;
  createClarification: (matterId: string, input: ClarificationInput) => Promise<Clarification>;
  respondClarification: (
    clarificationId: string,
    input: ClarificationRespondInput,
  ) => Promise<Clarification>;
  listGates: (matterId: string) => Promise<Gate[]>;
  createGate: (matterId: string, input: GateInput) => Promise<Gate>;
  decideGate: (gateId: string, input: GateDecisionInput) => Promise<Gate>;
}

/** Typed client for the LegacyFlow firm (attorney) API. */
export function createFirmApi(options: ApiClientOptions = {}): FirmApi {
  const call = <T>(
    path: string,
    init?: {
      method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
      body?: unknown;
      signal?: AbortSignal;
    },
  ): Promise<T> => apiFetch<T>(path, { ...options, ...init });

  return {
    listMatters: (filters = {}) => {
      const params = new URLSearchParams();
      if (filters.q?.trim()) params.set('q', filters.q.trim());
      if (filters.status) params.set('status', filters.status);
      if (filters.jurisdictionCode?.trim()) {
        params.set('jurisdiction_code', filters.jurisdictionCode.trim());
      }
      params.set('page', String(filters.page ?? 1));
      params.set('page_size', String(filters.pageSize ?? 20));
      return call<MattersListResponse>(`/matters?${params.toString()}`);
    },
    getMatter: (matterId) => call<MatterDetail>(`/matters/${matterId}`),
    generateReviewPacket: (matterId, input) =>
      call<ReviewPacketDetail>(`/matters/${matterId}/review-packets`, {
        method: 'POST',
        body: input ?? {},
      }),
    listReviewPackets: (matterId) =>
      call<ReviewPacketSummary[]>(`/matters/${matterId}/review-packets`),
    getReviewPacket: (matterId, packetId) =>
      call<ReviewPacketDetail>(`/matters/${matterId}/review-packets/${packetId}`),
    listComments: (matterId) => call<MatterComment[]>(`/matters/${matterId}/comments`),
    createComment: (matterId, input) =>
      call<MatterComment>(`/matters/${matterId}/comments`, { method: 'POST', body: input }),
    listClarifications: (matterId) => call<Clarification[]>(`/matters/${matterId}/clarifications`),
    createClarification: (matterId, input) =>
      call<Clarification>(`/matters/${matterId}/clarifications`, {
        method: 'POST',
        body: input,
      }),
    respondClarification: (clarificationId, input) =>
      call<Clarification>(`/clarifications/${clarificationId}/respond`, {
        method: 'POST',
        body: input,
      }),
    listGates: (matterId) => call<Gate[]>(`/matters/${matterId}/gates`),
    createGate: (matterId, input) =>
      call<Gate>(`/matters/${matterId}/gates`, { method: 'POST', body: input }),
    decideGate: (gateId, input) =>
      call<Gate>(`/gates/${gateId}/decide`, {
        method: 'POST',
        body: input,
      }),
  };
}

/**
 * Shared browser client for the firm workspace. Uses a no-op `getToken` until
 * the API auth integration lands (see note at the top of this file).
 */
export const firmApi: FirmApi = createFirmApi();
