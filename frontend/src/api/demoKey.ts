// Static demo (VITE_DEMO=1): the one function that names a recorded response. The app (src/api/client.ts) and
// the snapshot script (scripts/demo-snapshot.mjs) both import it, so the key a response is saved under is the
// key the app asks for. Plain erasable TypeScript only: Node runs this file directly.

/** FNV-1a, 32 bit, as 8 hex digits. Deterministic and dependency free. */
export function fnv1a(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

/**
 * `GET /api/runs/12/artifact?version=2` -> `runs__12__artifact~<hash of the sorted query>`.
 * The path is readable (without the `/api/` prefix), the query is sorted then hashed, a non-GET method is
 * prefixed. The same request always gives the same key; the extension is `demoExtension(path)`.
 */
export function demoKey(method: string, pathWithQuery: string): string {
  const q = pathWithQuery.indexOf("?");
  const path = q < 0 ? pathWithQuery : pathWithQuery.slice(0, q);
  const query = q < 0 ? "" : pathWithQuery.slice(q + 1);
  const readable = path
    .replace(/^\/+/, "")
    .replace(/^api\//, "")
    .replace(/\/+$/, "")
    .split("/")
    .map((seg) => seg.replace(/[^A-Za-z0-9._-]/g, "_"))
    .join("__");
  const pairs = [...new URLSearchParams(query).entries()].sort(([ak, av], [bk, bv]) => (ak === bk ? (av < bv ? -1 : av > bv ? 1 : 0) : ak < bk ? -1 : 1));
  const normal = pairs.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
  const prefix = method.toUpperCase() === "GET" ? "" : `${method.toLowerCase()}__`;
  return `${prefix}${readable || "root"}${normal ? `~${fnv1a(normal)}` : ""}`;
}

/** PDFs are saved as files (`file`, `pdf`, sheet pages); a kept original (Word, Excel or PDF) as `.bin`; the rest is JSON. */
export function demoExtension(path: string): "json" | "pdf" | "bin" {
  const p = path.split("?")[0] ?? "";
  if (/\/documents\/\d+\/original$/.test(p)) return "bin";
  if (/\/documents\/\d+\/file$/.test(p) || /\/sheet-pages\/\d+$/.test(p) || /\/issues\/\d+\/pdf$/.test(p)) return "pdf";
  return "json";
}

/** File name of a response inside `public/demo/`. */
export function demoFile(method: string, pathWithQuery: string): string {
  return `${demoKey(method, pathWithQuery)}.${demoExtension(pathWithQuery)}`;
}
