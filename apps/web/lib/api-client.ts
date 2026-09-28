/**
 * Typed client for the LegacyFlow family API (Tracks B/C contract).
 *
 * Token-agnostic by design: the JWT comes from the `getToken` callback so the
 * client never reaches into auth internals. The shared `api` / `firmApi`
 * exports below wire the browser auth adapter's access token (the Supabase
 * JWT in real mode); callers that need a different token can build their own
 * client with `createFamilyApi` / `createFirmApi`.
 */

import { getAuthAdapter } from './auth';
import { trackSave } from './save-status';

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

/** Download a binary response (generated demo documents). Throws ApiError on failure. */
export async function apiFetchBlob(
  path: string,
  options: ApiClientOptions & { signal?: AbortSignal; method?: 'GET' | 'POST' } = {},
): Promise<{ blob: Blob; filename: string }> {
  const { baseUrl = '', getToken, signal, method = 'POST' } = options;
  const headers: Record<string, string> = {};
  const token = await getToken?.();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const response = await fetch(`${baseUrl}/v1${path}`, { method, headers, signal });
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
  const blob = await response.blob();
  const disposition = response.headers.get('content-disposition') ?? '';
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  return { blob, filename: match?.[1] ?? 'document' };
}

/** Build the same-origin download URL for a file given a short-lived token. */
export function fileDownloadUrl(fileId: string, token: string, baseUrl = ''): string {
  return `${baseUrl}/v1/files/${fileId}/download?token=${encodeURIComponent(token)}`;
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

/* ------------------------------------------------------------------ files */

export type FileScanStatus = 'pending' | 'clean' | 'quarantined' | string;

export interface MatterFile {
  id: string;
  firm_id: string;
  matter_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  scan_status: FileScanStatus;
  uploaded_by_user_id: string;
  created_at: string;
}

export interface FileUploadIntentInput {
  filename: string;
  content_type: string;
  size_bytes: number;
}

export interface FileUploadIntent {
  upload_id: string;
  /** Pre-signed URL the browser PUTs the file bytes to directly. */
  upload_url: string;
}

export interface DownloadTokenResponse {
  download_token: string;
  expires_at: string;
}

/* ------------------------------------------------------------ ai assistant */

export interface AiCitation {
  pack: string;
  section: string;
}

export interface AiAskResponse {
  answer: string;
  citations: AiCitation[];
  refused: boolean;
  /** Safe completion shown when `refused` is true. */
  safe_message: string | null;
}

export interface AiAskInput {
  question: string;
}

/* -------------------------------------------------------------- documents */

export interface DocumentTemplate {
  id: string;
  firm_id: string;
  name: string;
  template_type: string;
  pack_id: string | null;
  version: string;
  content: string;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface DocumentTemplateCreateInput {
  name: string;
  template_type: string;
  pack_id?: string | null;
  version?: string;
  content: string;
}

export interface DocumentPackageInput {
  template_ids: string[];
  notes?: string | null;
}

export interface DocumentPackage {
  id: string;
  firm_id: string;
  matter_id: string;
  template_ids: string[];
  status: string;
  created_at: string;
}

export type DemoFormat = 'pdf' | 'docx';

export interface GeneratedDemo {
  package: DocumentPackage;
  versions: DemoDocument[];
}

export interface DemoDocument {
  id: string;
  package_id: string;
  version: number;
  file_format: 'docx' | 'pdf';
  file_name: string;
  content_type: string;
  watermark_text: string | null;
  is_demo: boolean;
  download_url: string;
  created_at: string;
}

/* ---------------------------------------------------------------- signing */

export interface SigningChecklistItem {
  key: string;
  label: string;
  detail: string | null;
}

export interface SigningRequirement {
  id: string;
  label: string;
  kind: string;
  detail: string | null;
}

export interface SigningAttestation {
  id: string;
  requirement_id: string;
  attested_at: string;
  attested_by_user_id: string;
}

export interface SigningChecklist {
  id: string;
  matter_id: string;
  execution_profile_id: string;
  document_type: string;
  requirements: Record<string, unknown>;
  items: SigningChecklistItem[];
  attestations: SigningAttestation[];
  completed_at: string | null;
  created_at: string;
}

export interface AttestInput {
  requirement_id: string;
  /** Non-certifying acknowledgement the client confirms before attesting. */
  acknowledgement: string;
}

/* ---------------------------------------------------------------- funding */

export type FundingTaskStatus = 'pending' | 'in_progress' | 'complete' | string;

export interface FundingTask {
  id: string;
  firm_id: string;
  matter_id: string;
  title: string;
  description: string | null;
  status: FundingTaskStatus;
  evidence_file_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface FundingTaskUpdateInput {
  status: FundingTaskStatus;
  note?: string | null;
  evidence_file_id?: string | null;
}

/* ------------------------------------------------------------------ vault */

export interface VaultShareInput {
  label: string;
  expires_at?: string | null;
}

export interface VaultShare {
  id: string;
  firm_id: string;
  matter_id: string;
  label: string;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface VaultShareCreated extends VaultShare {
  /** Bearer token shown exactly once at creation. */
  token: string;
}

export interface VaultDocumentSummary {
  filename: string;
  content_type: string;
}

export interface VaultAccessSummary {
  share_label: string;
  matter_title: string;
  jurisdiction_code: string;
  /** Always true: the public page only ever sees the redacted summary. */
  redacted: boolean;
  documents: VaultDocumentSummary[];
  expires_at: string | null;
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
  /* files (matter-scoped) */
  listFiles: (matterId: string) => Promise<MatterFile[]>;
  requestIntent: (matterId: string, input: FileUploadIntentInput) => Promise<FileUploadIntent>;
  completeUpload: (uploadId: string) => Promise<MatterFile>;
  getDownloadToken: (matterId: string, fileId: string) => Promise<DownloadTokenResponse>;
  /* ai assistant */
  askAi: (matterId: string, question: string) => Promise<AiAskResponse>;
  /* documents (demo, family-visible) */
  listDemoDocuments: (matterId: string) => Promise<DemoDocument[]>;
  downloadDemoDocument: (downloadUrl: string) => Promise<{ blob: Blob; filename: string }>;
  /* signing */
  getChecklist: (matterId: string) => Promise<SigningChecklist>;
  attest: (checklistId: string, input: AttestInput) => Promise<SigningAttestation>;
  /* funding */
  listFundingTasks: (matterId: string) => Promise<FundingTask[]>;
  updateFundingTask: (taskId: string, input: FundingTaskUpdateInput) => Promise<FundingTask>;
  /* vault */
  listShares: (matterId: string) => Promise<VaultShare[]>;
  createShare: (matterId: string, input: VaultShareInput) => Promise<VaultShareCreated>;
  revokeShare: (shareId: string) => Promise<VaultShare>;
  accessByToken: (token: string) => Promise<VaultAccessSummary>;
}

export function createFamilyApi(options: ApiClientOptions = {}): FamilyApi {
  const call = <T>(
    path: string,
    init?: {
      method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
      body?: unknown;
      signal?: AbortSignal;
      /** Set false for non-mutating POSTs (AI analysis) so they don't report save status. */
      track?: boolean;
    },
  ): Promise<T> => {
    const { track, ...fetchInit } = init ?? {};
    const promise = apiFetch<T>(path, { ...options, ...fetchInit });
    const method = init?.method ?? 'GET';
    const isMutation =
      method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE';
    return isMutation && track !== false ? trackSave(promise) : promise;
  };

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
      call<EvaluateResponse>(`/matters/${matterId}/intake/evaluate`, {
        method: 'POST',
        track: false,
      }),
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
        track: false,
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
    /* files */
    listFiles: (matterId) => call<MatterFile[]>(`/matters/${matterId}/files`),
    requestIntent: (matterId, input) =>
      call<FileUploadIntent>(`/matters/${matterId}/files/intent`, {
        method: 'POST',
        body: input,
      }),
    completeUpload: (uploadId) =>
      call<MatterFile>(`/files/uploads/${uploadId}/complete`, { method: 'POST' }),
    getDownloadToken: (matterId, fileId) =>
      call<DownloadTokenResponse>(`/matters/${matterId}/files/${fileId}/download-token`),
    /* ai assistant */
    askAi: (matterId, question) =>
      call<AiAskResponse>(`/matters/${matterId}/ai/ask`, {
        method: 'POST',
        track: false,
        body: { question } satisfies AiAskInput,
      }),
    /* documents (demo, family-visible) */
    listDemoDocuments: (matterId) => call<DemoDocument[]>(`/matters/${matterId}/document-versions`),
    downloadDemoDocument: (downloadUrl: string) => {
      // Backend returns "/v1/document-versions/{id}/download"; strip the /v1 prefix
      // since apiFetchBlob adds it.
      const path = downloadUrl.replace(/^\/v1/, '');
      return apiFetchBlob(path, { ...options, method: 'GET' });
    },
    /* signing */
    getChecklist: (matterId) => call<SigningChecklist>(`/matters/${matterId}/signing-checklist`),
    attest: (checklistId, input) =>
      call<SigningAttestation>(`/signing-checklists/${checklistId}/attest`, {
        method: 'POST',
        body: input,
      }),
    /* funding */
    listFundingTasks: (matterId) => call<FundingTask[]>(`/matters/${matterId}/funding-tasks`),
    updateFundingTask: (taskId, input) =>
      call<FundingTask>(`/funding-tasks/${taskId}`, { method: 'PATCH', body: input }),
    /* vault */
    listShares: (matterId) => call<VaultShare[]>(`/matters/${matterId}/vault-shares`),
    createShare: (matterId, input) =>
      call<VaultShareCreated>(`/matters/${matterId}/vault-shares`, {
        method: 'POST',
        body: input,
      }),
    revokeShare: (shareId) =>
      call<VaultShare>(`/vault-shares/${shareId}/revoke`, { method: 'POST' }),
    /* Public endpoint: never send a token, even if the shared client has one wired. */
    accessByToken: (token) =>
      apiFetch<VaultAccessSummary>(`/vault/access`, {
        ...options,
        getToken: () => null,
        method: 'POST',
        body: { token },
      }),
  };
}

/**
 * Shared browser client. Sends the signed-in user's Supabase access token as
 * the API Bearer <redacted> (null when signed out or in mock mode).
 */
export const api: FamilyApi = createFamilyApi({
  getToken: () => getAuthAdapter().getAccessToken(),
});

/* -------------------------------------------------- firm administration */

export interface FirmProfile {
  id: string;
  legal_name: string;
  display_name: string;
  status: 'active' | 'suspended' | 'trial';
  created_at: string;
}

export type FirmMemberRole = 'firm_admin' | 'attorney' | 'staff' | 'auditor';

export interface FirmMember {
  id: string;
  firm_id: string;
  user_id: string;
  role: FirmMemberRole;
  status: 'active' | 'invited' | 'disabled';
  created_at: string;
  display_name: string | null;
  email: string | null;
}

export interface EffectiveFeatureFlag {
  key: string;
  is_enabled: boolean;
  source: string;
}

export interface AuditEvent {
  id: string;
  firm_id: string | null;
  matter_id: string | null;
  actor_user_id: string | null;
  actor_type: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  correlation_id: string | null;
  before_summary: Record<string, unknown> | null;
  after_summary: Record<string, unknown> | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AuditEventFilters {
  action?: string;
  resourceType?: string;
  actorType?: string;
  matterId?: string;
  page?: number;
  pageSize?: number;
}

export interface AuditEventListResponse {
  items: AuditEvent[];
  page: number;
  page_size: number;
  total: number;
}

export interface ContentPack {
  jurisdiction_code: string;
  pack_id: string;
  display_name: string;
  version: string;
  status: string;
  effective_from: string | null;
}

export interface PackTransitionResult {
  id: string;
  pack_id: string;
  version: string;
  from_status: string;
  to_status: string;
}

export interface PackImportResult {
  pack_id: string;
  jurisdiction_code: string;
  version: string;
  status: string;
  source_bundle_hash: string;
  jurisdiction_id: string;
}

export interface FeatureFlagOut {
  id: string;
  key: string;
  scope: string;
  scope_ref: string;
  is_enabled: boolean;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Invitation {
  id: string;
  firm_id: string;
  email: string;
  role: string;
  household_id: string | null;
  status: string;
  expires_at: string;
  accepted_user_id: string | null;
  created_at: string;
}

export interface InvitationCreateInput {
  email: string;
  role: string;
  household_id?: string | null;
}

export interface InvitationCreateResponse {
  invitation: Invitation;
  accept_token: string | null;
}

export interface Escalation {
  id: string;
  matter_id: string;
  rule_id: string;
  severity: string;
  outcome: string;
  facts_snapshot: Record<string, unknown>;
  status: string;
  created_at: string;
}

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
  /* files (matter-scoped, read + download) */
  listFiles: (matterId: string) => Promise<MatterFile[]>;
  getDownloadToken: (matterId: string, fileId: string) => Promise<DownloadTokenResponse>;
  /* ai assistant */
  askAi: (matterId: string, question: string) => Promise<AiAskResponse>;
  /* documents */
  listTemplates: () => Promise<DocumentTemplate[]>;
  getTemplate: (templateId: string) => Promise<DocumentTemplate>;
  createTemplate: (input: DocumentTemplateCreateInput) => Promise<DocumentTemplate>;
  listPackages: (matterId: string) => Promise<DocumentPackage[]>;
  createPackage: (matterId: string, input: DocumentPackageInput) => Promise<DocumentPackage>;
  generateDemo: (packageId: string, format: DemoFormat) => Promise<GeneratedDemo>;
  releaseProduction: (packageId: string) => Promise<void>;
  /* signing */
  getChecklist: (matterId: string) => Promise<SigningChecklist>;
  /* funding */
  generateFundingTasks: (matterId: string) => Promise<FundingTask[]>;
  listFundingTasks: (matterId: string) => Promise<FundingTask[]>;
  /* invitations */
  listInvitations: () => Promise<Invitation[]>;
  createInvitation: (input: InvitationCreateInput) => Promise<InvitationCreateResponse>;
  revokeInvitation: (invitationId: string) => Promise<Invitation>;
  /* escalations */
  listEscalations: (matterId: string) => Promise<Escalation[]>;
}

/** Typed client for the LegacyFlow firm (attorney) API. */
export function createFirmApi(options: ApiClientOptions = {}): FirmApi {
  const call = <T>(
    path: string,
    init?: {
      method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
      body?: unknown;
      signal?: AbortSignal;
      /** Set false for non-mutating POSTs (AI analysis) so they don't report save status. */
      track?: boolean;
    },
  ): Promise<T> => {
    const { track, ...fetchInit } = init ?? {};
    const promise = apiFetch<T>(path, { ...options, ...fetchInit });
    const method = init?.method ?? 'GET';
    const isMutation =
      method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE';
    return isMutation && track !== false ? trackSave(promise) : promise;
  };

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
    /* files */
    listFiles: (matterId) => call<MatterFile[]>(`/matters/${matterId}/files`),
    getDownloadToken: (matterId, fileId) =>
      call<DownloadTokenResponse>(`/matters/${matterId}/files/${fileId}/download-token`),
    /* ai assistant */
    askAi: (matterId, question) =>
      call<AiAskResponse>(`/matters/${matterId}/ai/ask`, {
        method: 'POST',
        track: false,
        body: { question } satisfies AiAskInput,
      }),
    /* documents */
    listTemplates: () => call<DocumentTemplate[]>(`/document-templates`),
    getTemplate: (templateId) => call<DocumentTemplate>(`/document-templates/${templateId}`),
    createTemplate: (input) =>
      call<DocumentTemplate>(`/document-templates`, { method: 'POST', body: input }),
    listPackages: (matterId) => call<DocumentPackage[]>(`/matters/${matterId}/document-packages`),
    createPackage: (matterId, input) =>
      call<DocumentPackage>(`/matters/${matterId}/document-packages`, {
        method: 'POST',
        body: input,
      }),
    generateDemo: (packageId, format) =>
      call<GeneratedDemo>(`/packages/${packageId}/generate-demo`, {
        method: 'POST',
        body: { format },
      }),
    releaseProduction: (packageId) =>
      call<void>(`/packages/${packageId}/release-production`, { method: 'POST' }),
    /* signing */
    getChecklist: (matterId) => call<SigningChecklist>(`/matters/${matterId}/signing/checklist`),
    /* funding */
    generateFundingTasks: (matterId) =>
      call<FundingTask[]>(`/matters/${matterId}/funding-tasks/generate`, { method: 'POST' }),
    listFundingTasks: (matterId) => call<FundingTask[]>(`/matters/${matterId}/funding-tasks`),
    /* invitations */
    listInvitations: () => call<Invitation[]>(`/firms/invitations`),
    createInvitation: (input) =>
      call<InvitationCreateResponse>(`/firms/invitations`, { method: 'POST', body: input }),
    revokeInvitation: (invitationId) =>
      call<Invitation>(`/firms/invitations/${invitationId}/revoke`, { method: 'POST' }),
    /* escalations */
    listEscalations: (matterId) => call<Escalation[]>(`/matters/${matterId}/escalations`),
  };
}

/**
 * Shared browser client for the firm workspace. Sends the signed-in user's
 * Supabase access token as the API Bearer <redacted> (null when signed out or
 * in mock mode).
 */
export const firmApi: FirmApi = createFirmApi({
  getToken: () => getAuthAdapter().getAccessToken(),
});

/* -------------------------------------------------------------- admin api */

export interface AdminApi {
  getCurrentFirm: () => Promise<FirmProfile>;
  listFirmMembers: () => Promise<FirmMember[]>;
  listFeatureFlags: () => Promise<EffectiveFeatureFlag[]>;
  setFeatureFlag: (key: string, isEnabled: boolean) => Promise<FeatureFlagOut>;
  listAuditEvents: (filters?: AuditEventFilters) => Promise<AuditEventListResponse>;
  listPacks: () => Promise<ContentPack[]>;
  transitionPack: (packId: string, toStatus: string) => Promise<PackTransitionResult>;
  importPack: (input: {
    packDir?: string;
    bundle?: File;
    expectedBundleHash?: string;
  }) => Promise<PackImportResult>;
}

/** Typed client for the LegacyFlow firm-administration API (firm_admin only). */
export function createAdminApi(options: ApiClientOptions = {}): AdminApi {
  const call = <T>(
    path: string,
    init?: {
      method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
      body?: unknown;
      signal?: AbortSignal;
    },
  ): Promise<T> => {
    const { ...fetchInit } = init ?? {};
    const promise = apiFetch<T>(path, { ...options, ...fetchInit });
    const method = init?.method ?? 'GET';
    const isMutation =
      method === 'POST' || method === 'PUT' || method === 'PATCH' || method === 'DELETE';
    return isMutation ? trackSave(promise) : promise;
  };

  return {
    getCurrentFirm: () => call<FirmProfile>(`/firms/current`),
    listFirmMembers: () => call<FirmMember[]>(`/firms/members`),
    listFeatureFlags: () => call<EffectiveFeatureFlag[]>(`/admin/feature-flags`),
    setFeatureFlag: (key, isEnabled) =>
      call<FeatureFlagOut>(`/admin/feature-flags/${encodeURIComponent(key)}`, {
        method: 'PUT',
        body: { is_enabled: isEnabled },
      }),
    listAuditEvents: (filters = {}) => {
      const params = new URLSearchParams();
      if (filters.action?.trim()) params.set('action', filters.action.trim());
      if (filters.resourceType?.trim()) params.set('resource_type', filters.resourceType.trim());
      if (filters.actorType?.trim()) params.set('actor_type', filters.actorType.trim());
      if (filters.matterId?.trim()) params.set('matter_id', filters.matterId.trim());
      params.set('page', String(filters.page ?? 1));
      params.set('page_size', String(filters.pageSize ?? 50));
      return call<AuditEventListResponse>(`/admin/audit-events?${params.toString()}`);
    },
    listPacks: () => call<ContentPack[]>(`/packs`),
    transitionPack: (packId, toStatus) =>
      call<PackTransitionResult>(`/admin/packs/${encodeURIComponent(packId)}/transition`, {
        method: 'POST',
        body: { to_status: toStatus },
      }),
    importPack: async (input) => {
      const form = new FormData();
      if (input.packDir?.trim()) form.append('pack_dir', input.packDir.trim());
      if (input.bundle) form.append('bundle', input.bundle);
      if (input.expectedBundleHash?.trim()) {
        form.append('expected_bundle_hash', input.expectedBundleHash.trim());
      }
      const token = await options.getToken?.();
      const response = await fetch(`${options.baseUrl ?? ''}/v1/admin/packs/import`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: form,
      });
      if (!response.ok) {
        let parsed: unknown = null;
        try {
          parsed = await response.json();
        } catch {
          parsed = null;
        }
        const body = (parsed ?? {}) as { message?: string; detail?: string; code?: string };
        const message =
          body.message ?? body.detail ?? `Request failed with status ${response.status}.`;
        throw new ApiError(response.status, message, { code: body.code, details: parsed });
      }
      return (await response.json()) as PackImportResult;
    },
  };
}

/**
 * Shared browser client for firm administration. Sends the signed-in user's
 * Supabase access token as the API Bearer <redacted> Endpoints are firm_admin-only;
 * callers must handle 403s for other roles.
 */
export const adminApi: AdminApi = createAdminApi({
  getToken: () => getAuthAdapter().getAccessToken(),
});
