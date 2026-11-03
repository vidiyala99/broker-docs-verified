// Phase A voice directions (VOICE-BRIEF.md): a dev-only switch. `?voice=a|b|c` (before the hash) picks a direction
// for this tab, `?voice=off` clears it. Production builds and an unset switch return null, so the default app gets no
// new markup, CSS or fonts.
export type Voice = "a" | "b" | "c";

const KEY = "brokerdocs-voice";

function read(): Voice | null {
  if (!import.meta.env.DEV) return null;
  try {
    const q = new URLSearchParams(window.location.search).get("voice");
    if (q === "off") sessionStorage.removeItem(KEY);
    else if (q === "a" || q === "b" || q === "c") sessionStorage.setItem(KEY, q);
    const v = sessionStorage.getItem(KEY);
    return v === "a" || v === "b" || v === "c" ? v : null;
  } catch {
    return null;
  }
}

export const VOICE: Voice | null = read();

/** Sets `data-voice` on <html> and loads the direction's font and stylesheet. No-op without a voice. */
export function applyVoice(): void {
  if (!VOICE) return;
  document.documentElement.dataset.voice = VOICE;
  void import("./voice.css");
  if (VOICE === "a") {
    void import("@fontsource/ibm-plex-sans-condensed/500.css");
    void import("@fontsource/ibm-plex-sans-condensed/600.css");
  } else if (VOICE === "b") {
    void import("@fontsource-variable/archivo/wdth.css");
  } else {
    void import("@fontsource-variable/source-serif-4/opsz.css");
  }
}
