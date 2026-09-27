/**
 * @legacyflow/contracts — shared domain types for LegacyFlow Slice 1.
 *
 * Pure types only, plus a couple of tiny type-guard validators.
 * No runtime I/O, no legal content, no jurisdiction-specific behavior.
 */

/** Roles a user can hold inside a firm (§3.2). */
export type FirmRole = 'owner' | 'attorney' | 'paralegal' | 'staff' | 'viewer';

/** Status lifecycle of a jurisdiction content pack. */
export type JurisdictionPackStatus = 'draft' | 'in_review' | 'approved' | 'retired';

/** Minimal account profile. */
export interface Profile {
  id: string;
  email: string;
  displayName: string | null;
  createdAt: string;
  updatedAt: string;
}

/** A law firm / legal-services tenant on the platform. */
export interface Firm {
  id: string;
  name: string;
  createdAt: string;
  updatedAt: string;
}

/** Links a profile to a firm with a role. */
export interface FirmMembership {
  id: string;
  firmId: string;
  profileId: string;
  role: FirmRole;
  createdAt: string;
}

/** A supported legal jurisdiction (display only — behavior comes from packs). */
export interface Jurisdiction {
  code: string;
  name: string;
  country: string;
}

/** Capability flags a jurisdiction pack declares. */
export interface Capabilities {
  intake: boolean;
  education: boolean;
  eligibilityTriage: boolean;
  attorneyReview: boolean;
  documentAssembly: boolean;
  /** e.g. 'guided' | 'none' — free text so new modes don't break the contract. */
  executionGuidance: string;
  fundingGuidance: boolean;
}

/** Versioned content pack for a jurisdiction. */
export interface JurisdictionPack {
  id: string;
  jurisdictionCode: string;
  version: string;
  status: JurisdictionPackStatus;
  manifest: Capabilities;
  publishedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

/** A household — the family unit a matter is prepared for. */
export interface Household {
  id: string;
  firmId: string;
  primaryJurisdictionCode: string;
  createdAt: string;
  updatedAt: string;
}

/** Relationship of a member to the household (free-form labels come later). */
export type HouseholdMemberRole = 'head' | 'spouse' | 'child' | 'dependent' | 'other';

/** One person attached to a household. */
export interface HouseholdMember {
  id: string;
  householdId: string;
  displayName: string;
  role: HouseholdMemberRole;
  email: string | null;
  createdAt: string;
}

/** Lifecycle status of a client matter. */
export type MatterStatus =
  | 'intake'
  | 'attorney_review'
  | 'approved_for_assembly'
  | 'released'
  | 'signing'
  | 'funding'
  | 'plan_implemented'
  | 'closed';

/** A client matter: the unit of work between a household and a firm. */
export interface Matter {
  id: string;
  firmId: string;
  householdId: string;
  jurisdictionPackId: string;
  status: MatterStatus;
  createdAt: string;
  updatedAt: string;
}

/** Immutable record of a matter status transition. */
export interface MatterStatusHistory {
  id: string;
  matterId: string;
  fromStatus: MatterStatus | null;
  toStatus: MatterStatus;
  changedByProfileId: string | null;
  note: string | null;
  createdAt: string;
}

/** Immutable audit event for security-relevant actions. */
export interface AuditEvent {
  id: string;
  actorProfileId: string | null;
  action: string;
  resourceType: string;
  resourceId: string | null;
  /** Correlation id linking related events. */
  correlationId: string | null;
  /** Payload is redacted at write time — never raw PII. */
  payload: Record<string, string | number | boolean | null>;
  createdAt: string;
}

/* ------------------------------------------------------------------ */
/* Type-guard validators                                                */
/* ------------------------------------------------------------------ */

const FIRM_ROLES: readonly FirmRole[] = ['owner', 'attorney', 'paralegal', 'staff', 'viewer'];
const PACK_STATUSES: readonly JurisdictionPackStatus[] = ['draft', 'in_review', 'approved', 'retired'];
const MATTER_STATUSES: readonly MatterStatus[] = [
  'intake',
  'attorney_review',
  'approved_for_assembly',
  'released',
  'signing',
  'funding',
  'plan_implemented',
  'closed',
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0;
}

/** Narrow an unknown value to a valid {@link FirmRole}. */
export function isFirmRole(value: unknown): value is FirmRole {
  return typeof value === 'string' && (FIRM_ROLES as readonly string[]).includes(value);
}

/** Narrow an unknown value to a valid {@link JurisdictionPackStatus}. */
export function isJurisdictionPackStatus(value: unknown): value is JurisdictionPackStatus {
  return typeof value === 'string' && (PACK_STATUSES as readonly string[]).includes(value);
}

/** Narrow an unknown value to a valid {@link Matter}. */
export function isMatter(value: unknown): value is Matter {
  if (!isRecord(value)) return false;
  return (
    isNonEmptyString(value.id) &&
    isNonEmptyString(value.firmId) &&
    isNonEmptyString(value.householdId) &&
    isNonEmptyString(value.jurisdictionPackId) &&
    typeof value.status === 'string' &&
    (MATTER_STATUSES as readonly string[]).includes(value.status) &&
    isNonEmptyString(value.createdAt) &&
    isNonEmptyString(value.updatedAt)
  );
}
