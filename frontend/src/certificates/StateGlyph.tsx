import { CheckCircle, Eraser, Signature, Stamp, XCircle } from "@phosphor-icons/react";
import type { ClaimState } from "./certUtil";

/**
 * Claim state glyphs (CERTIFICATES-BRIEF.md): a ticked circle (supported), a crossed circle (not supported), a
 * signature (needs sign-off, bold; confirmed, regular), the broker's stamp (overridden), an eraser (removed). Each has
 * its own silhouette; the status word beside it carries the meaning, so the glyph is decorative.
 */
export function StateGlyph({ state, size = 18 }: { state: ClaimState; size?: number }) {
  const p = { size, "aria-hidden": true as const, className: `cstate cstate--${state}` };
  switch (state) {
    case "supported":
      return <CheckCircle {...p} />;
    case "not_supported":
      return <XCircle {...p} weight="bold" />;
    case "signoff":
      return <Signature {...p} weight="bold" />;
    case "confirmed":
      return <Signature {...p} />;
    case "overridden":
      return <Stamp {...p} />;
    case "removed":
      return <Eraser {...p} />;
  }
}
