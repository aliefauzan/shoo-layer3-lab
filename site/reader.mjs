// Reads live pages for the compare site: opens each URL once in headless Chromium (phone-sized, fresh profile, no
// downloads), takes a screenshot, turns the rendered HTML into a PageSnapshot with the Shoo eval parser, and runs the
// shipped pipeline on it (on-device layers 1-2, layer 3 rules, live domain age, layer 4 behind its 30..79 gate).
// Usage: node site/reader.mjs <ai/> <shots dir>   URLs as a JSON array on stdin; one JSON line per URL on stdout.
// The LLM key is read from ai/.dev.vars into memory only and never printed.
import { readFileSync, writeFileSync, mkdirSync, rmSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { execFile } from "node:child_process";
import { tmpdir } from "node:os";
import { chromium, devices } from "playwright-core";

const [repoAi, shotsDir] = process.argv.slice(2);
const here = dirname(fileURLToPath(import.meta.url));
const { loadDetector, normalizeSnapshot, scoreOf } = await import(join(repoAi, "eval/lib.mjs"));
const { analyzeWithDomain } = await import(join(repoAi, "src/analyze.js"));
const { readConfig } = await import(join(repoAi, "src/layer4/config.js"));
const { createRealityCheck } = await import(join(repoAi, "src/layer4/reality.js"));
const { createRdapClient } = await import(join(repoAi, "src/rdap/rdap.js"));
const { createMemoryCache } = await import(join(repoAi, "src/rdap/cache.js"));

const env = Object.fromEntries(
    readFileSync(join(repoAi, ".dev.vars"), "utf8")
        .split("\n")
        .filter((l) => /^[A-Z_]+=/.test(l))
        .map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1).trim().replace(/^["']|["']$/g, "")]),
);
const config = readConfig(env);
const checker = config ? createRealityCheck({ config: { ...config, dailyCap: 1_000_000 }, fetch, clientCallsPerHour: Infinity }) : null;
const rdap = createRdapClient({ fetch, cache: createMemoryCache(), timeoutMs: 4000 });
const detector = loadDetector();
mkdirSync(shotsDir, { recursive: true });

/** The row builder runs in its own process with a time limit: a hostile page can make a parser regex run for minutes. */
function buildRow(html, url) {
    const file = join(tmpdir(), `shoo-compare-${process.pid}-${Math.random().toString(36).slice(2)}.html`);
    writeFileSync(file, html);
    return new Promise((resolve) => {
        execFile("node", [join(here, "../src/build_snapshots.mjs"), repoAi, "--one", file, url], { timeout: 10000, maxBuffer: 64 * 1024 * 1024 }, (err, stdout) => {
            rmSync(file, { force: true });
            try {
                resolve(err ? null : JSON.parse(stdout));
            } catch {
                resolve(null);
            }
        });
    });
}

async function one(browser, url, n) {
    const context = await browser.newContext({ ...devices["iPhone 13"], acceptDownloads: false, serviceWorkers: "block", permissions: [] });
    try {
        const page = await context.newPage();
        page.on("dialog", (d) => d.dismiss().catch(() => {}));
        const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 20000 });
        await page.waitForTimeout(2000);
        const finalUrl = page.url();
        const shot = `${n}.jpg`;
        await page.screenshot({ path: join(shotsDir, shot), type: "jpeg", quality: 60 });
        const html = (await page.content()).slice(0, 2_000_000);
        const row = await buildRow(html, finalUrl);
        if (!row) return { url, finalUrl, status: response?.status() ?? null, shot, error: "Halaman terbuka, tapi isinya tidak bisa dibaca dalam 10 detik." };
        const { _snapshot, html: _structure, ...slim } = row;
        const snapshot = normalizeSnapshot(_snapshot);
        const local = (await detector.analyzeLocal(snapshot)).filter((s) => [1, 2].includes(s.layer));
        const report = {};
        const llm = checker ? { check: (snap, ctx) => checker.check(snap, { ...ctx, report }) } : null;
        const { signals, rdapRegisteredAt } = await analyzeWithDomain(snapshot, { rdap, llm, localSignals: local, client: "compare" });
        const all = [...local, ...signals];
        const fields = (snapshot.forms || []).flatMap((f) => f.fields || []);
        return {
            url,
            finalUrl,
            status: response?.status() ?? null,
            shot,
            row: slim,
            llmInput: { lang: snapshot.lang || null, fields: fields.slice(0, 20).map((f) => ({ type: f.type, name: f.name, label: f.label, placeholder: f.placeholder })), imageAlts: (snapshot.imageAlts || []).slice(0, 12) },
            read: { textLen: (snapshot.text || "").length, forms: (snapshot.forms || []).length, fields: fields.map((f) => f.type || f.name || "field").slice(0, 8), lang: snapshot.lang || null },
            imelda: {
                score: scoreOf(all),
                layer4: checker ? report.outcome ?? "gated" : "off",
                registeredAt: rdapRegisteredAt,
                signals: all.map((s) => ({ layer: s.layer, pts: s.weight ?? 0, label: s.label?.id ?? s.id, ev: typeof s.evidence === "string" ? s.evidence.slice(0, 200) : "" })),
            },
        };
    } catch (e) {
        const text = String(e?.message || e);
        const why = /ERR_NAME_NOT_RESOLVED/.test(text) ? "Domain tidak ditemukan (sudah mati atau salah ketik)." : /Timeout/.test(text) ? "Halaman tidak selesai dimuat dalam 20 detik." : /ERR_HTTP2|ERR_EMPTY_RESPONSE|ERR_BLOCKED/.test(text) ? "Situs menolak dibuka oleh browser otomatis.": /ERR_CONNECTION|ERR_SSL|ERR_CERT|ERR_ADDRESS/.test(text) ? "Tidak bisa tersambung ke situs ini." : "Halaman gagal dibuka.";
        return { url, error: why };
    } finally {
        await context.close().catch(() => {});
    }
}

const urls = JSON.parse(readFileSync(0, "utf8"));
const browser = await chromium.launch({ headless: true });
const queue = urls.map((url, n) => [url, n]);
await Promise.all(
    Array.from({ length: 3 }, async () => {
        while (queue.length) {
            const [url, n] = queue.shift();
            const result = await one(browser, url, n);
            process.stdout.write(JSON.stringify({ n, ...result }) + "\n");
        }
    }),
);
await browser.close();
