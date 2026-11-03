// Policy Check draft: lives in the browser until "Run check" (same lifetime rule as the proposal draft).
import type { DocumentInfo, PolicyRole } from "../api/types";

export type PcStep = 0 | 1 | 2;
export const PC_STEPS = ["Documents", "Roles", "Check"] as const;

/** Most PDFs a check can use (expiring, renewal, quote) and AMS exports (one). */
export const MAX_PDFS = 3;
export const MAX_AMS = 1;

export interface PcDraft {
  step: PcStep;
  reached: PcStep;
  pdfs: DocumentInfo[];
  ams: DocumentInfo[];
  /** Role -> document id. null = not assigned (an optional role then reads N/A in the report). */
  roles: Record<PolicyRole, number | null>;
  /** True once roles were guessed or set, so a later visit keeps the user's choice. */
  rolesSet: boolean;
  /** Work queue W4: the optional Client field on step 1, sent as `client_name`. */
  client_name: string;
}

export const EMPTY_ROLES: Record<PolicyRole, number | null> = { expiring: null, quote: null, renewal: null, ams: null };

export const EMPTY_PC_DRAFT: PcDraft = { step: 0, reached: 0, pdfs: [], ams: [], roles: EMPTY_ROLES, rolesSet: false, client_name: "" };

const KEY = "brokerdocs.policyCheckDraft.v1";

export function loadPcDraft(): PcDraft {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return EMPTY_PC_DRAFT;
    const d = JSON.parse(raw) as PcDraft;
    return { ...EMPTY_PC_DRAFT, ...d, roles: { ...EMPTY_ROLES, ...d.roles }, step: d.step === 2 ? 1 : d.step };
  } catch {
    return EMPTY_PC_DRAFT;
  }
}

export function savePcDraft(d: PcDraft) {
  try {
    if (isBlankPc(d)) sessionStorage.removeItem(KEY);
    else sessionStorage.setItem(KEY, JSON.stringify(d));
  } catch {
    /* storage full or blocked: the draft just does not survive a reload */
  }
}

export function clearPcDraft() {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

export function isBlankPc(d: PcDraft): boolean {
  return d.pdfs.length === 0 && d.ams.length === 0;
}

export interface RoleInfo {
  role: PolicyRole;
  name: string;
  required: boolean;
  /** What the role is, in the broker's terms. */
  about: string;
  kind: "pdf" | "ams";
}

export const ROLE_INFO: RoleInfo[] = [
  { role: "expiring", name: "Expiring policy", required: true, about: "The policy that is ending. The renewal is compared against it.", kind: "pdf" },
  { role: "renewal", name: "Renewal", required: true, about: "The document being checked. Every other document is compared to it.", kind: "pdf" },
  { role: "quote", name: "Quote", required: false, about: "The renewal quote, if you have one.", kind: "pdf" },
  { role: "ams", name: "AMS export", required: false, about: "The policy record from your agency system, as JSON or CSV.", kind: "ams" },
];

export const ROLE_LABEL: Record<PolicyRole, string> = { expiring: "Expiring", quote: "Quote", renewal: "Renewal", ams: "AMS" };

/** First guess from file names; the user confirms or changes it on the Roles step. */
export function guessRoles(pdfs: DocumentInfo[], ams: DocumentInfo[]): Record<PolicyRole, number | null> {
  const roles: Record<PolicyRole, number | null> = { ...EMPTY_ROLES };
  const used = new Set<number>();
  const take = (role: PolicyRole, re: RegExp) => {
    const d = pdfs.find((p) => !used.has(p.id) && re.test(p.filename));
    if (d) {
      roles[role] = d.id;
      used.add(d.id);
    }
  };
  take("expiring", /expir|current|prior/i);
  take("renewal", /renew/i);
  take("quote", /quote|proposal/i);
  for (const role of ["expiring", "renewal"] as const) {
    if (roles[role] == null) {
      const d = pdfs.find((p) => !used.has(p.id));
      if (d) {
        roles[role] = d.id;
        used.add(d.id);
      }
    }
  }
  roles.ams = ams[0]?.id ?? null;
  return roles;
}

/** Client mirror of the server's slot rules (POST /api/runs, 400 invalid_run). */
export function validateRoles(d: PcDraft): Partial<Record<PolicyRole, string>> {
  const errors: Partial<Record<PolicyRole, string>> = {};
  const pdfIds = new Set(d.pdfs.map((p) => p.id));
  const amsIds = new Set(d.ams.map((p) => p.id));
  for (const info of ROLE_INFO) {
    const id = d.roles[info.role];
    if (id == null) {
      if (info.required) errors[info.role] = `Choose the ${info.name.toLowerCase()} PDF.`;
      continue;
    }
    if (!(info.kind === "ams" ? amsIds : pdfIds).has(id)) errors[info.role] = "This document is no longer in the check. Choose another.";
  }
  const ids = Object.values(d.roles).filter((v): v is number => v != null);
  if (new Set(ids).size !== ids.length) errors.quote = errors.quote ?? "A document can fill only one role.";
  return errors;
}
