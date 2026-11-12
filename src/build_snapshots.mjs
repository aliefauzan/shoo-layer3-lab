// HTML -> one JSONL row per page: PageSnapshot fields (Imelda's parser, imported read-only), the current layer 3
// rule result (baseline), and extra structure features the current layer 3 does not read.
// Usage: node src/build_snapshots.mjs <path to Shoo repo ai/> <data/scamweb> <out.jsonl>
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { createHash } from "node:crypto";
import { join } from "node:path";
import { Worker, isMainThread, parentPort } from "node:worker_threads";
import { fileURLToPath } from "node:url";

const [repoAi, dataDir, outPath] = process.argv.slice(2);
const { parseHtml } = await import(join(repoAi, "eval/tools/snapshot.mjs"));
const { analyzeSnapshot } = await import(join(repoAi, "src/analyze.js"));
const { isOfficialHost } = await import(join(repoAi, "src/layer3/official.js"));
const { SHARED_HOSTS } = await import(join(repoAi, "src/blocklist/allowlist.js"));
const brandData = JSON.parse(readFileSync(join(repoAi, "../detector/data/brands.json"), "utf8"));
const BRAND_NAMES = brandData.brands.map((b) => [...new Set([b.name, ...(b.names || []), ...(b.commonNames || [])].map((n) => n.toLowerCase()))]);
const CHEAP_TLDS = new Set((brandData.cheapTlds || []).map((t) => t.replace(/^\./, "").toLowerCase()));
const URL_KEYWORDS = (brandData.keywords || []).map((k) => String(k).toLowerCase());
const esc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const BRAND_WORD = BRAND_NAMES.map((names) => new RegExp(`(^|[^a-z0-9])(${names.map(esc).join("|")})([^a-z0-9]|$)`, "i"));

/** Brand and address facts. Brand list and official domains are Shoo's own (detector/data/brands.json). */
function addressFacts(snap, host, url) {
    const official = isOfficialHost(host);
    const count = (text) => BRAND_WORD.filter((re) => re.test(text)).length;
    const inTitle = count(snap.title || "");
    const inText = count((snap.text || "").slice(0, 6000));
    const pieces = host.split(/[.\-_0-9]+/).filter(Boolean);
    const inHost = BRAND_NAMES.some((names) => names.some((n) => (n.length >= 5 ? host.includes(n.replace(/\s+/g, "")) : pieces.includes(n))));
    let path = "";
    try {
        path = new URL(url).pathname;
    } catch {}
    const labels = host.split(".");
    const shared = [...SHARED_HOSTS].some((h) => host === h || host.endsWith("." + h));
    const lowered = (host + path).toLowerCase();
    return {
        official,
        brandsInTitle: inTitle,
        brandsInText: inText,
        brandOffsite: !official && inTitle > 0,
        brandTextOffsite: !official && inText > 0,
        brandInHost: !official && inHost,
        cheapTld: CHEAP_TLDS.has(labels[labels.length - 1]),
        sharedHost: shared,
        hostLen: host.length,
        hostDigits: (host.match(/[0-9]/g) || []).length,
        hostHyphens: (host.match(/-/g) || []).length,
        subdomains: Math.max(0, labels.length - site(host).split(".").length),
        pathLen: path.length,
        pathDepth: path.split("/").filter(Boolean).length,
        ipHost: /^[0-9.]+$/.test(host),
        punycode: host.includes("xn--"),
        urlKeywords: URL_KEYWORDS.filter((k) => lowered.includes(k)).length,
    };
}

const WAYBACK = /https?:\/\/web\.archive\.org\/web\/\d+[a-z_]*\//gi;
// Rough registrable domain. Country second levels (co.id, go.id, ac.id, co.uk, com.au ...) need three labels.
const SECOND_LEVEL = /^(co|go|ac|or|web|sch|my|biz|net|com|org|gov|edu|mil|desa|ponpes)\.[a-z]{2}$/;
const site = (host) => {
    const p = host.replace(/^www\./, "").split(".");
    return p.slice(SECOND_LEVEL.test(p.slice(-2).join(".")) ? -3 : -2).join(".");
};
const hostOf = (u) => {
    try {
        return new URL(u).hostname.toLowerCase();
    } catch {
        return "";
    }
};

/** Held-out split by site, so pages of one site never sit in both train and test. */
function splitOf(host, train = 0.7, dev = 0.15) {
    const n = createHash("sha256").update(site(host)).digest()[0] / 256;
    return n < train ? "train" : n < train + dev ? "dev" : "test";
}

function structure(html, pageHost) {
    const hrefs = [...html.matchAll(/<a\b[^>]*\shref\s*=\s*["']?([^"'\s>]+)/gi)].map((m) => m[1]);
    const actions = [...html.matchAll(/<form\b[^>]*\saction\s*=\s*["']?([^"'\s>]+)/gi)].map((m) => m[1]);
    const abs = (list) => list.filter((h) => /^https?:\/\//i.test(h)).map(hostOf).filter(Boolean);
    const linkHosts = abs(hrefs);
    const external = linkHosts.filter((h) => site(h) !== site(pageHost)).length;
    return {
        links: hrefs.length,
        externalLinkShare: linkHosts.length ? external / linkHosts.length : 0,
        formActionOffsite: abs(actions).some((h) => site(h) !== site(pageHost)),
        apkLink: hrefs.some((h) => /\.apk(\?|#|$)/i.test(h)),
        chatLink: hrefs.some((h) => /(^|\/\/)(wa\.me|api\.whatsapp\.com|chat\.whatsapp\.com|t\.me|telegram\.me)\//i.test(h)),
        iframes: (html.match(/<iframe\b/gi) || []).length,
        hiddenInputs: (html.match(/<input\b[^>]*type\s*=\s*["']?hidden/gi) || []).length,
        scripts: (html.match(/<script\b/gi) || []).length,
    };
}

/** A page cut off at the size cap can end inside a <script> or <style>; that tail is code, not page text. */
function dropOpenTail(html) {
    for (const tag of ["script", "style"]) {
        const open = html.toLowerCase().lastIndexOf("<" + tag);
        if (open > html.toLowerCase().lastIndexOf("</" + tag)) html = html.slice(0, open);
    }
    return html;
}

function buildRow(rawHtml, url, host) {
    const html = dropOpenTail(rawHtml);
    const snap = parseHtml(html, { url, domain: host }).snapshot;
    return { ...rowFromSnapshot(snap, host, url), html: structure(html.replace(/<!--[\s\S]*?-->/g, ""), host), _snapshot: snap };
}

/** Everything the model may use comes from the PageSnapshot contract, so it also works on what the extension sends. */
function rowFromSnapshot(snap, host, url) {
    const signals = analyzeSnapshot(snap); // current layer 3, on the untouched snapshot
    const forms = snap.forms || [];
    const fields = forms.flatMap((x) => x.fields || []);
    const noBanner = (t) => (t || "").replace(/The Wayback Machine - \S+\s*/g, ""); // archive banner, not page content
    const text = noBanner(snap.text);
    const offsite = forms.some((f) => /^https?:\/\//i.test(f.action || "") && site(hostOf(f.action)) !== site(host));
    return {
        host,
        title: noBanner(snap.title),
        text,
        baseline: { score: signals.reduce((s, x) => s + (x.weight ?? 0), 0), signals: signals.map((s) => s.id) },
        feats: {
            textLen: text.length,
            fields: fields.length,
            passwordField: fields.some((x) => x.type === "password"),
            forms: forms.length,
            imageAlts: (snap.imageAlts || []).length,
            formActionOffsite: offsite,
            ...addressFacts({ title: noBanner(snap.title), text }, host, url),
        },
    };
}

/** Writes rows (without their snapshot) and, for test pages, the full snapshot to <out>.snapshots.jsonl (layer 4 needs it). */
function writeRows(path, rows) {
    const strip = ({ _snapshot, ...r }) => r;
    writeFileSync(path, rows.map((r) => JSON.stringify(strip(r))).join("\n") + "\n");
    const test = rows.filter((r) => r._snapshot && r.split === "test");
    writeFileSync(path.replace(/\.jsonl$/, ".snapshots.jsonl"), test.map((r) => JSON.stringify({ id: r.id, snapshot: r._snapshot })).join("\n") + "\n");
}

// Worker mode: builds one row per message, so the main thread can stop a page whose parsing never ends.
if (!isMainThread) {
    parentPort.on("message", ({ html, url, host }) => {
        try {
            parentPort.postMessage({ row: buildRow(html, url, host) });
        } catch {
            parentPort.postMessage({ row: null });
        }
    });
    await new Promise(() => {});
}

/** buildRow with a time limit. Some hostile pages make a parser regex run for minutes. */
let worker = null;
function buildRowGuarded(html, url, host, ms = 4000) {
    return new Promise((resolve) => {
        worker ??= new Worker(fileURLToPath(import.meta.url), { argv: process.argv.slice(2) });
        const timer = setTimeout(() => {
            worker.terminate();
            worker = null;
            resolve(null);
        }, ms);
        worker.once("message", ({ row }) => {
            clearTimeout(timer);
            resolve(row);
        });
        worker.postMessage({ html, url, host });
    });
}

// A folder of fetched pages: node src/build_snapshots.mjs <ai/> --dir <out.jsonl> <dir> <label 0|1> <id prefix> <language|-> [train] [dev]
// Files are *.html.gz with the page URL on line 1.
if (dataDir === "--dir") {
    const [dir, label, prefix, language, train = "0.7", dev = "0.15", captured = new Date().toISOString().slice(0, 10)] = process.argv.slice(5);
    const out = [];
    let skipped = 0;
    for (const f of readdirSync(dir).filter((x) => x.endsWith(".html.gz")).sort()) {
        const raw = gunzipSync(readFileSync(join(dir, f))).toString("utf8");
        const cut = raw.indexOf("\n");
        const url = raw.slice(0, cut).trim();
        const host = hostOf(url);
        if (!host) continue;
        try {
            const row = await buildRowGuarded(raw.slice(cut + 1), url, host);
            if (!row) {
                skipped++;
                continue;
            }
            if (row.text.length < 150) continue; // empty shells (JavaScript-only pages, bare redirects) teach nothing
            out.push({ id: `${prefix}_${f.replace(".html.gz", "")}`, label: Number(label), scamType: null, language: language === "-" ? null : language, archived: false, source: prefix, url, captured, split: splitOf(host, Number(train), Number(dev)), ...row });
        } catch {}
    }
    writeRows(outPath, out);
    console.log(prefix, "pages", out.length, "sites", new Set(out.map((r) => site(r.host))).size, "current rules >=30:", out.filter((r) => r.baseline.score >= 30).length, "too slow to parse:", skipped);
    process.exit(0);
}

// Imelda's eval corpus (Indonesian, already PageSnapshots): node src/build_snapshots.mjs <ai/> --corpus <out.jsonl>
if (dataDir === "--corpus") {
    const files = ["legit/real.json", "legit/traps.json", "scam/real.json", "scam/seed.json", "scam/synthetic.json"];
    const out = [];
    for (const f of files) {
        for (const s of JSON.parse(readFileSync(join(repoAi, "eval/data", f), "utf8"))) {
            if (!s.snapshot?.text) continue;
            const host = hostOf(s.snapshot.url);
            out.push({ id: s.id, label: s.label === "scam" ? 1 : 0, synthetic: !!s.synthetic, tags: s.tags || [], file: f, split: "corpus", url: s.snapshot.url, captured: "2026-10-22", ...rowFromSnapshot(s.snapshot, host, s.snapshot.url) });
        }
    }
    writeFileSync(outPath, out.map((r) => JSON.stringify(r)).join("\n") + "\n");
    console.log("corpus pages", out.length, "scam", out.filter((r) => r.label).length, "synthetic", out.filter((r) => r.synthetic).length);
    process.exit(0);
}

// Single page mode: node src/build_snapshots.mjs <ai/> --one <saved.html> <url>  -> one JSON row on stdout
if (dataDir === "--one") {
    const [, , , , htmlPath, url] = process.argv;
    console.log(JSON.stringify(buildRow(readFileSync(htmlPath, "utf8"), url, hostOf(url))));
    process.exit(0);
}

const meta = new Map(
    readFileSync(join(dataDir, "meta.jsonl"), "utf8")
        .trim()
        .split("\n")
        .map((l) => JSON.parse(l))
        .map((m) => [m.sample_id.replace("/", "_"), m]),
);

const rows = [];
for (const f of readdirSync(join(dataDir, "html"))) {
    if (!f.endsWith(".html.gz")) continue;
    const id = f.replace(".html.gz", "");
    const m = meta.get(id);
    if (!m) continue;
    const raw = gunzipSync(readFileSync(join(dataDir, "html", f))).toString("utf8");
    const loc = (raw.match(/^<!--LOC:(.*?)-->/) || [])[1] || "";
    const archived = /web\.archive\.org\/web\//i.test(loc);
    let url = /^https?:\/\//i.test(loc) ? loc.replace(WAYBACK, "") : m.url || "";
    if (!/^https?:\/\//i.test(url)) url = "http://" + url;
    const host = hostOf(url);
    if (!host) continue;
    const html = raw.replace(WAYBACK, ""); // archived pages: give links and form targets their original address
    let row;
    try {
        row = buildRow(html, url, host);
    } catch {
        continue;
    }
    const stamp = (loc.match(/web\.archive\.org\/web\/(\d{4})(\d{2})(\d{2})/) || []).slice(1);
    rows.push({
        id,
        url,
        captured: stamp.length ? stamp.join("-") : "2026-01-01", // live captures: Nov 2025 to Feb 2026 (dataset card)
        label: m.category === "scam" ? 1 : 0,
        scamType: m.scam_type ? m.scam_type.replace(" / ", "/") : null,
        language: m.language,
        archived,
        split: splitOf(host),
        ...row,
    });
}
writeRows(outPath, rows);
const count = (pred) => rows.filter(pred).length;
console.log("pages", rows.length, "scam", count((r) => r.label), "benign", count((r) => !r.label));
for (const s of ["train", "dev", "test"]) console.log(s, count((r) => r.split === s), "scam", count((r) => r.split === s && r.label));
console.log("baseline >=30: scam", count((r) => r.label && r.baseline.score >= 30), "benign", count((r) => !r.label && r.baseline.score >= 30));
