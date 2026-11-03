import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "./styles/tokens.css";
import "./styles/app.css";
import { App } from "./App";
import { applyVoice } from "./voice/voice";

applyVoice(); // dev-only Phase A switch (VOICE-BRIEF.md); no-op by default

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
