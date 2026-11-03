// Shared pointer-vs-keyboard helper (motion audit B4; DESIGN.md Motion: keyboard-triggered changes are instant).
// Tracks the last input the person used and mirrors it on <html data-input="pointer|keyboard">, so CSS can drop
// transitions for keyboard changes (`:root[data-input="keyboard"] .x { transition: none }`) and components can ask
// before animating (`if (!keyboardAction()) animate`).
export type InputMode = "pointer" | "keyboard";

let mode: InputMode = "pointer";
let at = 0;

function set(next: InputMode) {
  mode = next;
  at = performance.now();
  if (document.documentElement.dataset.input !== next) document.documentElement.dataset.input = next;
}

/** Call once at startup. Capture phase, so it runs before any handler that animates. */
export function initInputMode(): void {
  document.documentElement.dataset.input = mode;
  window.addEventListener("pointerdown", () => set("pointer"), true);
  window.addEventListener(
    "keydown",
    (e) => {
      // Modifier keys alone are not an action (Ctrl while clicking, Shift for a range).
      if (e.key === "Shift" || e.key === "Control" || e.key === "Alt" || e.key === "Meta") return;
      set("keyboard");
    },
    true,
  );
}

/** The change being rendered came from the keyboard: show it instantly. */
export function keyboardAction(): boolean {
  return mode === "keyboard";
}

/** The person used a pointer within the last `withinMs` (for motion that only answers a pointer action). */
export function pointerAction(withinMs = 2000): boolean {
  return mode === "pointer" && performance.now() - at < withinMs;
}
