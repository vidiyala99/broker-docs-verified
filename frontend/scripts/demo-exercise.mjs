// Shared by demo-snapshot.mjs and demo-qa.mjs: look at a page by clicking only its read-only controls.

/** Controls we may click while looking: read-only ones. Anything that writes or leaves the page is matched out. */
const WRITES = /^(mark|accept|use |add|edit|issue|override|confirm|remove|undo|reopen|export|regenerate|move|change|send|create|dismiss|good|bad|copy|new version|retry|cancel|delete|save|clear|apply|resolve|start|build|skip|upload|generate|download|reset|back)/i;
const LOOKS = /^(chat|report|input parameters|upload|recent work|show |hide |side by side|original source|verified|read from|couldn't verify|edited|all \d|critical|high|medium|low|over \$|open only|last \d|open reserve|claim|claimant|loss date|status|paid|reserve|incurred|source |review one|matches|supported|issued|summary|policies|files|rows|older)/i;

/** Click every read-only control on the current page once (toggles are clicked back), so the UI asks for what they load. */
export async function exercise(page, { cap = 80 } = {}) {
  const labels = await page.evaluate(() => {
    const out = [];
    for (const e of document.querySelectorAll("button, [role=tab]")) {
      const label = (e.getAttribute("aria-label") || e.textContent || "").trim().replace(/\s+/g, " ");
      if (label && !e.disabled && e.offsetParent !== null) out.push(label);
    }
    return out;
  });
  const todo = [...new Set(labels)].filter((l) => LOOKS.test(l) && !WRITES.test(l)).slice(0, cap);
  for (const label of todo) {
    const el = page.getByRole("button", { name: label, exact: true }).first();
    try {
      if (!(await el.isVisible({ timeout: 300 })) || !(await el.isEnabled())) continue;
      const pressed = await el.getAttribute("aria-pressed");
      await el.click({ timeout: 2000 });
      await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});
      await page.waitForTimeout(250);
      if (pressed === "false") {
        const again = page.getByRole("button", { name: label, exact: true }).first();
        if ((await again.count()) && (await again.getAttribute("aria-pressed")) === "true") {
          await again.click({ timeout: 2000 }).catch(() => {});
          await page.waitForTimeout(150);
        }
      }
      await page.keyboard.press("Escape");
    } catch {
      /* a control that moved or vanished while we looked: skip it */
    }
  }
}

