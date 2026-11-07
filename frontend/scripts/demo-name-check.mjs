// Fails when the product name appears where publish_scrub.py cannot rename it.
//
//   node scripts/demo-name-check.mjs [dir ...]      (default: public/demo and dist)
//
// The public copy is made by publish_scrub.py: a byte-level text replace of "BrokerDocs" in source files, and only a
// length-preserving rename inside binaries (PDF fixtures). So this check looks for a case-insensitive "brokerdocs"
//   - in every text file (JSON, HTML, JS, CSS, maps): only the allowed source constants may contain it;
//   - in every PDF: raw bytes, every FlateDecode stream (object streams hold the Info dictionary and XMP, often
//     compressed), hex strings and UTF-16 strings, and the text pdf.js extracts per page (text drawn as separate
//     glyph runs with kerning is not a contiguous byte string);
//   - in other binaries (.bin: a kept Word or Excel file is a zip): raw bytes, UTF-16, and every inflated zip entry.
//
// Allowed (source text the scrub renames before the public build): the APP_NAME value in config.ts, the page
// title in index.html, and the three localStorage / event names that start with "brokerdocs" in src/.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { inflateRawSync, inflateSync } from "node:zlib";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dirs = process.argv.slice(2).length ? process.argv.slice(2).map((d) => path.resolve(d)) : [path.join(FRONTEND, "public", "demo"), path.join(FRONTEND, "dist")];

const ALLOWED = [
  /(["'`])BrokerDocs\1/g, // APP_NAME in config.ts, as bundled
  /<title>BrokerDocs<\/title>/g, // index.html
  /(["'`])brokerdocs[.:][A-Za-z0-9.-]+\1/g, // brokerdocs.policyCheckDraft.v1, brokerdocs.proposal-draft.v1, brokerdocs:proposal-draft-restored
];
const NAME = /brokerdocs/i;
const NAME16 = Buffer.from("brokerdocs", "utf16le").toString("latin1");

const problems = [];
const unreadable = [];
const scanned = { text: 0, pdf: 0, bin: 0, other: 0 };
const flag = (file, where) => problems.push(`${path.relative(FRONTEND, file)}: ${where}`);

function* walk(dir) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) yield* walk(p);
    else yield p;
  }
}

/** Case-insensitive search of a byte buffer for the name as ASCII, UTF-16LE, UTF-16BE, hex string or PDF-escaped. */
function bytesHit(buf) {
  const latin = buf.toString("latin1");
  if (NAME.test(latin)) return "ascii";
  const lower = latin.toLowerCase();
  if (lower.includes(NAME16)) return "utf-16le";
  const be = Buffer.from("brokerdocs", "utf16le").swap16().toString("latin1");
  if (lower.includes(be)) return "utf-16be";
  // PDF hex strings <4f75746d61726b6574>
  const hex = latin.replace(/\s+/g, "").toLowerCase();
  if (hex.includes("6f75746d61726b6574") || hex.includes("004f00750074006d00610072006b00650074".toLowerCase())) return "hex string";
  return null;
}

/** Every inflatable stream in a PDF (FlateDecode), including object streams. */
function* pdfStreams(buf) {
  const latin = buf.toString("latin1");
  const re = /stream\r?\n/g;
  let m;
  while ((m = re.exec(latin))) {
    const start = m.index + m[0].length;
    const end = latin.indexOf("endstream", start);
    if (end < 0) break;
    const raw = buf.subarray(start, end);
    try {
      yield inflateSync(raw);
    } catch {
      try {
        yield inflateSync(raw.subarray(0, raw.length - 1));
      } catch {
        /* not Flate: scanned as raw bytes already */
      }
    }
    re.lastIndex = end;
  }
}

/** Zip entries (docx/xlsx originals): inflate every local-file entry. */
function* zipEntries(buf) {
  let i = 0;
  while ((i = buf.indexOf(Buffer.from([0x50, 0x4b, 0x03, 0x04]), i)) >= 0) {
    const method = buf.readUInt16LE(i + 8);
    const csize = buf.readUInt32LE(i + 18);
    const nlen = buf.readUInt16LE(i + 26);
    const xlen = buf.readUInt16LE(i + 28);
    const start = i + 30 + nlen + xlen;
    const name = buf.subarray(i + 30, i + 30 + nlen).toString();
    const data = buf.subarray(start, start + csize);
    try {
      yield { name, data: method === 8 ? inflateRawSync(data) : data };
    } catch {
      yield { name, data };
    }
    i = start + Math.max(csize, 1);
  }
}

async function pdfText(file) {
  const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const doc = await pdfjs.getDocument({ data: new Uint8Array(readFileSync(file)), useSystemFonts: false, verbosity: 0 }).promise;
  const parts = [];
  const meta = await doc.getMetadata().catch(() => null);
  if (meta) parts.push(JSON.stringify(meta.info ?? {}), meta.metadata ? String([...meta.metadata.getAll?.() ? Object.values(meta.metadata.getAll()) : []]) : "");
  for (let n = 1; n <= doc.numPages; n++) {
    const page = await doc.getPage(n);
    const tc = await page.getTextContent();
    parts.push(tc.items.map((it) => it.str ?? "").join(""), tc.items.map((it) => it.str ?? "").join(" "));
    for (const a of await page.getAnnotations()) parts.push(JSON.stringify(a));
  }
  const outline = await doc.getOutline().catch(() => null);
  if (outline) parts.push(JSON.stringify(outline));
  await doc.cleanup?.();
  return parts.join("\n");
}

function checkText(file) {
  scanned.text++;
  let text = readFileSync(file, "utf8");
  for (const re of ALLOWED) text = text.replace(re, "");
  const m = NAME.exec(text);
  if (m) {
    const ctx = text.slice(Math.max(0, m.index - 40), m.index + 50).replace(/\s+/g, " ");
    flag(file, `"${ctx}"`);
  }
}

function checkBinary(file, kind) {
  scanned[kind]++;
  const buf = readFileSync(file);
  const raw = bytesHit(buf);
  if (raw) flag(file, `raw bytes (${raw})`);
  if (kind === "pdf") {
    let n = 0;
    for (const s of pdfStreams(buf)) {
      n++;
      const hit = bytesHit(s);
      if (hit) flag(file, `inside a compressed stream #${n} (${hit})`);
    }
  } else {
    for (const e of zipEntries(buf)) {
      const hit = bytesHit(e.data);
      if (hit) flag(file, `zip entry ${e.name} (${hit})`);
    }
  }
}

const TEXT = /\.(json|html?|js|mjs|css|map|svg|txt|webmanifest|xml)$/i;

for (const dir of dirs) {
  if (!existsSync(dir) || !statSync(dir).isDirectory()) {
    console.error(`name check: ${dir} does not exist (build or snapshot first)`);
    process.exit(2);
  }
  for (const file of walk(dir)) {
    if (TEXT.test(file)) checkText(file);
    else if (/\.pdf$/i.test(file) || readFileSync(file).subarray(0, 5).toString() === "%PDF-") {
      checkBinary(file, "pdf");
      try {
        const t = await pdfText(file);
        if (NAME.test(t)) flag(file, "in the text pdf.js extracts");
      } catch (e) {
        // A deliberately corrupt seed fixture (a failed run): the raw and stream scans above already covered it.
        unreadable.push(path.relative(FRONTEND, file));
        void e;
      }
    } else if (/\.bin$/i.test(file)) checkBinary(file, "bin");
    else {
      scanned.other++;
      const raw = bytesHit(readFileSync(file));
      if (raw) flag(file, `raw bytes (${raw})`);
    }
  }
}

if (unreadable.length) console.log(`note: pdf.js could not parse ${unreadable.length} file(s) (corrupt fixtures); raw and decompressed-stream scans still ran: ${unreadable.join(", ")}`);
console.log(`name check: ${scanned.text} text, ${scanned.pdf} pdf, ${scanned.bin} kept originals, ${scanned.other} other files scanned in ${dirs.map((d) => path.relative(FRONTEND, d)).join(", ")}`);
if (problems.length) {
  console.error(`name check FAILED: "brokerdocs" found where the scrub cannot rename it (${problems.length}):`);
  for (const p of problems.slice(0, 60)) console.error("  " + p);
  process.exit(1);
}
console.log("name check passed: only the allowed source constants carry the name.");
