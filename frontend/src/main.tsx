import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
// The paper layer: Source Serif 4 at the one weight the voice uses (600), latin subsets, font-display swap.
import "@fontsource/source-serif-4/latin-600.css";
import "@fontsource/source-serif-4/latin-ext-600.css";
import "./styles/tokens.css";
import "./styles/app.css";
import { App } from "./App";
// After App: the voice layer overrides component stylesheets (DESIGN.md Voice).
import "./voice/voice.css";
import { initInputMode } from "./ui/input";

initInputMode(); // pointer vs keyboard for motion (DESIGN.md Motion)

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
