# Slice map

Date: 2026-11-02. Owner decisions recorded here; each item still gets its own design pass
(brainstorm, spec, design stack for UI) before any code.
Evidence: `docs/reference/brokerdocs-workflows-evidence.md`, `../BrokerDocs-REA-Teardown.md`, `rea-out/shots/`.

## Built
| Slice | Workflow | Rail item | Status |
|---|---|---|---|
| 1 | Compare Quotes (core engine) | Workflows | merged |
| 2 | Proposal Builder | Proposals | merged |
| 3 | Policy Check | Policy check | backend merged; UI (T9) in final bug-fix pass |

## Next: slice 3b, rail completion (owner decision 2026-11-02)
Goal: every rail item is either a working screen or an honest mock. No invented data presented as real.

| Rail item | Plan | Evidence |
|---|---|---|
| Personal lines | Filtered entry into existing workflows (Compare Quotes, Policy Check) for homeowners and auto documents. No new engine. | Marketing page screenshot: homeowners quote comparison with citation chips (`rea-out/shots/personal-lines.png`) |
| Tasks | Broker-defined tasks: the broker creates and manages their own items. Proposed shape (to confirm in design): title, optional due date, optional link to a run or discrepancy, status open/done; "create task" from a Policy Check discrepancy. | No screen observed; app endpoint string `/automations/needs-you` only |
| Integrations | Mock page: connect cards (Applied Epic, AMS360, Nexsure, ImageRight, HawkSoft, Gmail, Google Drive, OneDrive, Outlook, SharePoint) with a visible note that integrations are not designed yet and this page is a mock. Connect buttons do nothing real. | `rea-out/shots/integrations.png` |

## Later slices (from the evidence ranking)
| Slice | Workflow | Rail item |
|---|---|---|
| 4 | COI and form filler (own layout first) | Certificates |
| 5 | Loss Run (spreadsheet parsing, aggregation, dashboards) | Analytics / Insights |
| 6 | Market Search and Vault-wide assistant | Market search, Assistant |
| unscheduled | Data-only pack: Contract Requirements, Coverage Gap, Tower Analysis | Workflows |
| unscheduled | Vault (document store with folders) | Vault, Documents |
| deferred | ACORD application fill beyond COI (after slice 4) | Certificates |

Insights/Analytics: the only observed screen (Data Intelligence forecast, `yt/frame_0033` to `0035`) needs
agency-wide data the clone does not have; tie it to Loss Run data in slice 5 rather than invent numbers.
