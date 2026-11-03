# Slice map

Date: 2026-11-02, updated 2026-11-03. Owner decisions recorded here; each item still gets its own design pass
(brainstorm, spec, design stack for UI) before any code.
Evidence: `docs/reference/brokerdocs-workflows-evidence.md`, `../BrokerDocs-REA-Teardown.md`, `rea-out/shots/`.

## Built (all merged to main)
| Slice | Workflow | Rail item | Notes |
|---|---|---|---|
| 1 | Compare Quotes (core engine) | Workflows | Verified citations, long documents (300 pages), live eval 268/286, 0 hard fails |
| 2 | Proposal Builder | Proposals | Wizard, outline editing, DOCX export; live eval 0.939 |
| 3 | Policy Check | Policy check | Expiring / renewal / quote / AMS discrepancies; live recall 13/13 |
| 3b | Rail completion | Personal lines, Tasks, Integrations | Personal lines = filtered entry; broker-defined Tasks; Integrations is a labelled mock |
| (extra) | Work queue + Activity | Workflows, Personal lines | Queue is home (`docs/specs/2026-11-02-work-queue-design.md`); raw runs live in Activity |
| (extra) | Voice "Paper trail" + motion | everywhere | Serif for people, drawn marks, rail Silhouettes; DESIGN.md Voice/Motion |
| (extra) | Line move (Commercial / Personal) | run header | Book tab + "Move to ..." button, book-first crumbs, Undo |

## In flight: slice 4 (owner decision 2026-11-03)
| Slice | Workflow | Rail item |
|---|---|---|
| 4 | COI and form filler (own layout first, no observed screen) | Certificates |

## Later slices (from the evidence ranking)
| Slice | Workflow | Rail item |
|---|---|---|
| 5 | Loss Run (spreadsheet parsing, aggregation, dashboards) | Analytics / Insights |
| 6 | Market Search, Vault (document store with folders over uploaded files) and Vault-wide assistant | Market search, Assistant, Vault |
| unscheduled | Data-only pack: Contract Requirements, Coverage Gap, Tower Analysis | Workflows |

Vault joins slice 6 (owner decision 2026-11-03): the assistant needs a document store underneath, and with it every
rail item is live once slice 6 ships.
| deferred | ACORD application fill beyond COI (after slice 4) | Certificates |

Data for slices 5 and 6 (owner decision 2026-11-03): real carrier appetite, loss runs and agency history are beyond
our control, so these slices use synthetic data (generated appetite guides, product sheets, loss-run files), shown
with a visible "synthetic sample data" label per PRODUCT.md ("mocks say they are mocks"). The engine still applies
verify-before-cite to the synthetic documents; general-knowledge Assistant answers stay out or are labelled not cited.

Insights/Analytics: the only observed screen (Data Intelligence forecast, `yt/frame_0033` to `0035`) needs
agency-wide data the clone does not have; tie it to Loss Run data in slice 5 rather than invent numbers.

## Open owner decisions (not slices)
Template library, Standard-template live eval, Proposal mark everywhere, left-panel strip icons.
