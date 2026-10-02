# LegacyFlow — Production Readiness & Attorney Marketplace Plan
**Date:** 2026-10-02 | **Status:** DRAFT for Venugopal's review

---

## Part 1: Feature Gap Analysis — What's Pending

### Backend (API): ~85% complete
The API is in good shape. These exist and work:
- Matters, households, intake (guided), interview (AI), documents, files
- Review packets, comments, clarifications (attorney ↔ family communication)
- Document packages (create, demo, release production), document versions
- Signing checklists, funding tasks, vault shares
- Matter state machine (DRAFT → INTAKE → ATTORNEY_REVIEW → APPROVED → DOCUMENTS_RELEASED → SIGNING → FUNDING → IMPLEMENTED)
- Firm roles: `firm_admin`, `attorney`, `staff`, `auditor`

### Frontend (UI): ~60% complete — THE GAPS ARE HERE
| Feature | Backend | UI | Notes |
|---|---|---|---|
| Family household/matter creation | ✅ | ❌ | API-only. Family sees "No matters yet" with no way to create. |
| Document package creation | ✅ | ❌ | API-only. No "Prepare package" button anywhere. |
| Review packet workflow | ✅ | ❌ | `firm/review-queue` is just a matter list; doesn't use review-packets API. |
| Attorney review UI | ✅ | ❌ | No screen for attorney to see packet, comment, request clarification, approve. |
| Signing checklist UI | ✅ | ❌ | API exists; no UI for family to see/attest signing steps. |
| Funding tasks UI | ✅ | ❌ | API exists; no UI. |
| Document assembly | ❌ | ❌ | Virginia pack has `document_assembly:false`. Returns 409. No template engine wired. |
| Attorney onboarding | ❌ | ❌ | No flow at all. |
| Attorney marketplace | ❌ | ❌ | No case pickup, assignment, or payment. |
| E-signature / Notary | ❌ | ❌ | Future. |

### Critical Path to "Functional" Production
For the product to be *usable* end-to-end (not just demoable), these are non-negotiable:
1. **Family can create a matter** (currently impossible via UI)
2. **Attorney can review a packet** (currently impossible via UI)
3. **Document package can be generated** (API exists; needs UI trigger + `document_assembly` enabled for at least one pack)
4. **Matter can move through the state machine via UI** (submit → review → approve → release)

Without these four, it's a demo, not a product.

---

## Part 2: Attorney Marketplace — Brainstorm & Model

### The Core Idea
Attorneys onboard as independent contractors. They pick up cases from a queue (or get assigned). They review the AI-prepared intake package, request clarifications or approve. They get paid per completed review. LegacyFlow takes a platform fee.

### How It Should Work

**Attorney Onboarding:** MANUAL ONLY (Venugopal decision, 2026-10-02). No automation.
- Attorney signs up → provides bar number, jurisdictions licensed, uploads (bar card, malpractice insurance, ID)
- Goes into admin verification queue
- Venugopal/team manually verifies via state bar website, reviews docs, clicks Approve/Reject
- Venugopal will onboard first 4-5 attorneys personally
- System must have: signup form, document upload, admin review queue, approve/reject actions, role assignment

**Case Flow:**
1. Family completes intake (AI interview + guided) → matter status `INTAKE_SUBMITTED`
2. System creates a **review packet** (intake answers, AI-extracted facts, consistency flags, documents)
3. Case appears in the **marketplace queue** (visible to attorneys licensed in that jurisdiction)
4. Attorney **claims** the case (first-come) OR system **assigns** (round-robin / load-balanced)
5. Matter status → `ATTORNEY_REVIEW`. Attorney has SLA (e.g., 48 hours).
6. Attorney reviews packet:
   - **Approve** → matter → `APPROVED_FOR_ASSEMBLY`. Attorney signs off (digital attestation).
   - **Request clarification** → matter → `CLARIFICATION_REQUESTED`. Family gets notified, responds, matter → back to `ATTORNEY_REVIEW`.
   - **Escalate** → matter → `ESCALATED` (complex case needs senior attorney or specialist).
7. After approval, document package is generated → `DOCUMENTS_RELEASED`
8. Attorney marks review **complete** → triggers payment.

**Payment Model:**
- Per-case flat fee (e.g., $150 for standard will review, $300 for complex/trust)
- Fee set by LegacyFlow (not negotiated per case) — simple, predictable
- Attorney payouts via Stripe Connect, batched weekly
- LegacyFlow takes platform fee (e.g., 20-30%)
- Payment triggers on: attorney marks complete AND family doesn't dispute within 7 days
- Dispute process: family can flag issues → hold payment → manual review

**What the Attorney Sees (UI):**
- Dashboard: active cases, earnings, SLA timers, ratings
- Case detail: review packet (all intake data, AI notes, consistency flags), family documents, clarification thread
- Actions: Approve / Request Clarification / Escalate / Complete
- Earnings page: per-case breakdown, payout history, tax documents

### What Should NOT Be There (v1)
- ❌ **Attorney bidding/auction** — too complex, race-to-the-bottom on quality. Fixed fees only.
- ❌ **Direct attorney-family messaging** — keep all communication in the structured clarification thread (audit trail, prevents off-platform deals).
- ❌ **Attorney setting their own prices** — LegacyFlow sets fees for consistency.
- ❌ **Real-time chat with attorney** — async review only. This is not a law firm chat app.
- ❌ **Attorney editing family documents directly** — they review and approve; the system generates. Prevents unauthorized practice issues.
- ❌ **Public attorney profiles/ratings visible to families** — families don't choose attorneys in v1 (system assigns). Avoids popularity contests.
- ❌ **Online notary in v1** — separate regulatory beast. Phase 2.

### Trust & Safety (Non-Negotiable)
- Bar verification before any case access
- Every attorney action logged (audit trail)
- Attorney never sees family PII beyond what's needed for review (data minimization)
- Malpractice insurance required, verified annually
- LegacyFlow is a **marketplace**, not a law firm — clear Terms that attorneys are independent contractors
- **Unauthorized Practice of Law (UPL)**: The AI does intake only. Only licensed attorneys approve. This boundary is the entire legal foundation — never blur it.

---

## Part 3: Phased Production Plan

### Phase 1: Make It Functional (2-3 weeks)
Goal: A family can go from signup to attorney-approved package without API hacks.

1. **Family matter creation UI** — "Start new estate plan" button on family home
2. **Attorney review UI** — packet view, approve/clarify/escalate actions
3. **Document package UI** — "Prepare package" button (firm-side), triggers API
4. **Enable `document_assembly`** for Virginia pack (or stub with clearly-labeled demo docs)
5. **State machine UI wiring** — buttons for each transition (submit, approve, release)
6. **Basic attorney onboarding** — manual verification (form + admin approval), no automation
7. **Manual case assignment** — firm_admin assigns cases to attorneys (no marketplace yet)
8. **Modern Fintech re-theme** (already approved)

### Phase 2: Attorney Marketplace (3-4 weeks)
Goal: Attorneys self-onboard, pick up cases, get paid.

1. **Self-service attorney signup** with document upload (bar card, insurance)
2. **Verification queue** for LegacyFlow ops team
3. **Marketplace queue UI** — available cases, claim button, SLA timers
4. **Attorney dashboard** — active cases, earnings, history
5. **Stripe Connect integration** — payouts, platform fee split
6. **Automated payment triggers** — on completion + dispute window
7. **Clarification thread UI** — structured attorney ↔ family communication
8. **SLA monitoring** — auto-reassign if attorney misses deadline

### Phase 3: Scale & Polish (4+ weeks)
- Online notary integration (Notarize, Proof)
- E-signature (DocuSign, Dropbox Sign)
- Automated bar verification (state bar APIs)
- Attorney performance analytics
- Family-facing attorney selection (if needed)
- Mobile app (if web traction validates)

---

## Part 4: What Should NOT Be Built (Ever, or Not Yet)

| Feature | Verdict | Reason |
|---|---|---|
| AI generating final legal documents | **Never** | UPL risk. AI does intake; attorneys approve; templates generate. |
| AI giving legal advice | **Never** | Already have the boundary. Keep it. |
| Attorney-client direct messaging | **Not in v1** | Audit trail + prevents disintermediation. |
| Video calls with attorney | **Not in v1** | Async is the model. Calls don't scale. |
| DIY legal documents without attorney | **Never** | Defeats the purpose. The attorney is the product. |
| Cryptocurrency payments | **No** | Unnecessary complexity. |
| Social features / community | **No** | Not a social product. |

---

## Open Questions for Venugopal

1. **Pricing**: What should the family pay? What should the attorney get per case? (This determines unit economics.)
2. **Jurisdiction**: Virginia only for launch, or multi-state from day one?
3. **Attorney sourcing**: Do you have attorneys lined up, or do we need to recruit?
4. **Manual vs automated**: For v1, is manual attorney verification + manual case assignment acceptable? (Faster to launch.)
5. **Document assembly**: Should I build a real template engine, or stub with clearly-labeled demo docs for now?
