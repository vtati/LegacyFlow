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
}

export interface MattersListResponse {
  items: MatterSummary[];
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
  };
}

/**
 * Shared browser client. Uses a no-op `getToken` until the API auth integration
 * lands (see note at the top of this file).
 */
export const api: FamilyApi = createFamilyApi();
