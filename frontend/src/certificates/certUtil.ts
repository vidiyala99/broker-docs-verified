import type { CertificateBlocked, CertificateView, ClaimItem, Run, Wording } from "../api/types";

/** `workflow_name` of slice 4 certificate runs (backend seed). */
export const CERTIFICATE_WORKFLOW = "Certificate of Insurance";
export const isCertificateRun = (run: Pick<Run, "workflow_name"> | null | undefined) => run?.workflow_name === CERTIFICATE_WORKFLOW;

export const MAX_POLICY_PDFS = 8;
/** D6 bounds (backend/API.md "Creating one"). */
export const LIMITS = { holder_name: 120, holder_address: 300, description: 1500 } as const;

/**
 * The legal wording of the own layout (C6). Mirrors backend/export_certificate.py DISCLAIMER, CANCELLATION and
 * SAMPLE_NOTE: the owner asked (2026-11-03) for the clone's own wording to be marked as a sample wherever it prints.
 */
export const DISCLAIMER =
  "This certificate is issued as a matter of information only and confers no rights on the certificate holder. It does not amend, extend or alter the coverage of the policies listed. Every value printed here was checked against the policy documents; a value that could not be verified is printed as Not stated.";
export const CANCELLATION =
  "Should any of the policies above be cancelled before their expiration dates, notice will be delivered in line with the policy provisions.";
export const SAMPLE_NOTE = "Sample wording: replace with your agency's approved certificate language before use.";

export const WORDING_LABEL: Record<Wording, string> = {
  additional_insured: "Additional insured",
  waiver_of_subrogation: "Waiver of subrogation",
  primary_noncontributory: "Primary and non-contributory",
};
export const WORDINGS: Wording[] = ["additional_insured", "waiver_of_subrogation", "primary_noncontributory"];

/**
 * What a claim means for the broker now. Silhouette (glyph) first, colour second, the word always present.
 * `needs_action` is the server's truth for "still open" (a removed or overridden claim reopens when its evidence
 * changes, `changed_since_resolved`).
 */
export type ClaimState = "supported" | "not_supported" | "signoff" | "overridden" | "confirmed" | "removed";

export const STATE_WORD: Record<ClaimState, string> = {
  supported: "Supported",
  not_supported: "Not supported",
  signoff: "Needs sign-off",
  overridden: "Overridden",
  confirmed: "Confirmed",
  removed: "Removed",
};

export function stateOf(c: ClaimItem): ClaimState {
  if (c.needs_action) return c.verdict === "needs_signoff" ? "signoff" : "not_supported";
  if (c.status === "removed") return "removed";
  if (c.verdict === "supported") return "supported";
  return c.verdict === "needs_signoff" ? "confirmed" : "overridden";
}

export const needsYou = (s: ClaimState) => s === "not_supported" || s === "signoff";
export const handled = (s: ClaimState) => s === "overridden" || s === "confirmed" || s === "removed";
/** Ledger and margin order: what needs the broker first. */
export const STATE_ORDER: ClaimState[] = ["not_supported", "signoff", "overridden", "confirmed", "removed", "supported"];

export function letterOf(c: ClaimItem): string | null {
  if (c.policy_letter) return c.policy_letter;
  if (c.kind === "edited") return c.key.split(":")[1] ?? null;
  return null;
}

/** Where a claim sits on the certificate, so the ledger and the paper can point at each other. */
export function regionOf(c: ClaimItem): string {
  const letter = letterOf(c);
  if (c.kind === "wording") return `p-${letter}-${c.wording}`;
  if (c.kind === "in_force") return `p-${letter}-dates`;
  if (c.kind === "edited") {
    const field = c.key.split(":").slice(2).join(":");
    return letter === "insured" ? "insured" : `f-${letter}-${field}`;
  }
  return c.kind; // insured, description
}

/** The verb on the Remove button, named by what it does to the paper (D13). */
export function removeLabel(c: ClaimItem): string {
  if (c.kind === "edited") return "Restore the extracted value";
  if (c.kind === "description") return "Remove the description";
  if (c.kind === "in_force") return "Remove this policy";
  return "Remove from certificate";
}

/** Plain explanations for the Issue 409s (D5, D13) and for the blocked list of the view. */
export const BLOCKED_TEXT: Record<CertificateBlocked | "version_changed" | "issue_failed", string> = {
  run_active: "A document is still being read. Issue when it finishes.",
  claims_open: "Some checks still need you.",
  no_coverage: "No printed policy is in force on the certificate date. Add a document or put a policy back.",
  issued: "This version is issued. Start a new version to change it.",
  no_draft: "There is no draft to issue yet.",
  version_changed: "The draft changed since you opened it. Check it again, then issue.",
  issue_failed: "The PDF could not be made, so nothing was issued. Retry.",
};

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  // Server timestamps are UTC without an offset (API.md); a bare date is a calendar date.
  const d = /^\d{4}-\d{2}-\d{2}$/.test(iso)
    ? new Date(`${iso}T00:00:00`)
    : new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/** "11/03/2026", as the certificate prints it. */
export function printedDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString("en-US", { month: "2-digit", day: "2-digit", year: "numeric" });
}

export const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

export const isIssued = (v: CertificateView) => v.issue != null && v.issue.version === v.version;
export const isDraftEditable = (v: CertificateView) => !isIssued(v) && !v.run.active && v.certificate != null;

export const certificateNewHref = (client?: string | null) =>
  `#/certificates/new${client ? `?client=${encodeURIComponent(client)}` : ""}`;
