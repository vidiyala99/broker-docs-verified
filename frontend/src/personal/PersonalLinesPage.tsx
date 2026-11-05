import type { Workspace } from "../api/types";
import { LaunchView } from "../launch/LaunchView";

/**
 * Slice 3b T6 page, work queue lane (spec section 2): the personal-lines work queue under the header and the two entry
 * points that open the Compare Quotes and Policy Check screens with Personal preselected. Since slice 6 it is the same
 * book home as Commercial (launch/LaunchView.tsx, COMPARE-MATCH-BRIEF.md). The raw run list lives in Activity.
 */
export function PersonalLinesPage({ workspace }: { workspace: Workspace }) {
  return <LaunchView workspace={workspace} line="personal" />;
}
