// DG7: the product name comes from this one constant. Rename here if the app ever leaves the machine.
export const APP_NAME = "BrokerDocs";

// The backend listens on 127.0.0.1 only (backend/API.md). Override with VITE_API_BASE.
export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://127.0.0.1:8000";

export const WORKFLOW_NAME = "Compare Quotes";

/** `workflow_name` of slice 2 proposal runs (backend seed). */
export const PROPOSAL_WORKFLOW = "Proposal Builder";

/** `workflow_name` of slice 3 Policy Check runs (backend seed). */
export const POLICY_WORKFLOW = "Policy Check";
