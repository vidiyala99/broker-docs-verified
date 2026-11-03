import type { ReactNode } from "react";
import { Database, EnvelopeSimple, FolderSimple, Info } from "@phosphor-icons/react";
import { Tip } from "../ui/Tip";
import "./integrations.css";

/**
 * Slice 3b T5: Integrations mock (owner decision R7). Static: vendor names as text with a generic
 * category glyph, no logos, Connect is inert, no connection states, no network calls.
 * Decisions in frontend/INTEGRATIONS-BRIEF.md.
 */

export const MOCK_NOTICE = "Integrations are not designed yet. This page is a mock; nothing connects.";

type Category = "ams" | "dms" | "email" | "files";

const CATEGORY: Record<Category, { label: string; icon: ReactNode }> = {
  ams: { label: "Agency management system", icon: <Database size={20} aria-hidden /> },
  dms: { label: "Document management system", icon: <Database size={20} aria-hidden /> },
  email: { label: "Email", icon: <EnvelopeSimple size={20} aria-hidden /> },
  files: { label: "File storage", icon: <FolderSimple size={20} aria-hidden /> },
};

interface Vendor {
  name: string;
  category: Category;
}

export const GROUPS: { title: string; vendors: Vendor[] }[] = [
  {
    title: "Agency management systems",
    vendors: [
      { name: "Applied Epic", category: "ams" },
      { name: "AMS360", category: "ams" },
      { name: "Nexsure", category: "ams" },
      { name: "HawkSoft", category: "ams" },
      { name: "ImageRight", category: "dms" },
    ],
  },
  {
    title: "Documents and email",
    vendors: [
      { name: "Gmail", category: "email" },
      { name: "Outlook", category: "email" },
      { name: "Google Drive", category: "files" },
      { name: "OneDrive", category: "files" },
      { name: "SharePoint", category: "files" },
    ],
  },
];

const MOCK_DESC_ID = "integrations-mock-desc";

export function IntegrationsPage() {
  return (
    <div className="workspace">
      <main className="pane integrations" id="main" tabIndex={-1} aria-labelledby="integrations-title">
        <div className="integrations__body">
          <header className="integrations__head">
            <h1 id="integrations-title" className="page-title">
              Integrations
            </h1>
            <p className="notice integrations__notice" role="note">
              <Info size={20} aria-hidden />
              <span className="notice__text">{MOCK_NOTICE}</span>
            </p>
          </header>
          <span id={MOCK_DESC_ID} className="sr-only">
            Mock. Nothing connects.
          </span>
          {GROUPS.map((group) => {
            const headingId = `integrations-${group.title.toLowerCase().replace(/\s+/g, "-")}`;
            return (
              <section key={group.title} className="integrations__group" aria-labelledby={headingId}>
                <h2 id={headingId} className="integrations__group-title">
                  {group.title}
                </h2>
                <ul className="integrations__grid">
                  {group.vendors.map((v) => (
                    <li key={v.name} className="integ-card">
                      <span className="integ-card__icon">{CATEGORY[v.category].icon}</span>
                      <h3 className="integ-card__name">{v.name}</h3>
                      <p className="integ-card__desc">{CATEGORY[v.category].label}</p>
                      <div className="integ-card__foot">
                        <Tip text="Mock">
                          <button
                            type="button"
                            className="btn btn--secondary btn--sm"
                            aria-disabled="true"
                            aria-label={`Connect ${v.name}`}
                            aria-describedby={MOCK_DESC_ID}
                            onClick={(e) => e.preventDefault()}
                          >
                            Connect
                          </button>
                        </Tip>
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      </main>
    </div>
  );
}
