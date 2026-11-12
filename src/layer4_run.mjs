// Runs Imelda's real layer 4 (her prompt, her model, settings from ai/.dev.vars) on held-out pages, two ways:
//   imelda   her full pipeline as shipped: on-device layers 1-2 (detector/), layer 3 rules, layer 4 behind HER gate (30..79)
//   rebuilt  the same layer 4 call, but opened by the rebuilt layer 3 tier (gate or warn) instead of her score
// Usage: node src/layer4_run.mjs <path to Shoo repo ai/> out
// Reads out/tiers.json ({id: tier}) and out/*.snapshots.jsonl (+ Imelda's corpus files). Appends to out/layer4.jsonl;
// rerunning skips ids already there. Not simulated: the live blocklist (layer 1) and domain age (RDAP), because the
// test pages are historical. The LLM key is read into memory only and never printed.
import { readFileSync, readdirSync, appendFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const [repoAi, outDir] = process.argv.slice(2);
const { loadDetector, normalizeSnapshot, scoreOf } = await import(join(repoAi, "eval/lib.mjs"));
const { analyzeWithDomain } = await import(join(repoAi, "src/analyze.js"));
const { readConfig } = await import(join(repoAi, "src/layer4/config.js"));
const { createRealityCheck } = await import(join(repoAi, "src/layer4/reality.js"));
const { isOfficialHost, hostOf } = await import(join(repoAi, "src/layer3/official.js"));

const env = Object.fromEntries(
    readFileSync(join(repoAi, ".dev.vars"), "utf8")
        .split("\n")
        .filter((l) => /^[A-Z_]+=/.test(l))
        .map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1).trim().replace(/^["']|["']$/g, "")]),
);
const config = readConfig(env);
if (!config) throw new Error("layer 4 is not configured in ai/.dev.vars");
const checker = createRealityCheck({ config: { ...config, dailyCap: 100000 }, fetch, clientCallsPerHour: Infinity });
const detector = loadDetector();

const tiers = JSON.parse(readFileSync(join(outDir, "tiers.json"), "utf8"));
const snapshots = new Map();
for (const f of readdirSync(outDir).filter((x) => x.endsWith(".snapshots.jsonl"))) {
    for (const line of readFileSync(join(outDir, f), "utf8").split("\n")) {
        if (!line) continue;
        const { id, snapshot } = JSON.parse(line);
        snapshots.set(id, snapshot);
    }
}
for (const f of ["legit/real.json", "legit/traps.json", "scam/real.json", "scam/seed.json", "scam/synthetic.json"]) {
    for (const s of JSON.parse(readFileSync(join(repoAi, "eval/data", f), "utf8"))) if (s.snapshot?.text) snapshots.set(s.id, s.snapshot);
}

const outPath = join(outDir, "layer4.jsonl");
const done = new Set(existsSync(outPath) ? readFileSync(outPath, "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l).id) : []);
const todo = Object.keys(tiers).filter((id) => snapshots.has(id) && !done.has(id));
console.log("pages", Object.keys(tiers).length, "with snapshot", [...snapshots.keys()].filter((id) => id in tiers).length, "to run", todo.length, "model", checker.meta.model, "prompt", checker.meta.promptVersion);

async function one(id) {
    const snapshot = normalizeSnapshot(snapshots.get(id));
    const local = (await detector.analyzeLocal(snapshot)).filter((s) => [1, 2].includes(s.layer));
    // Imelda's pipeline as shipped (no RDAP client: domain age cannot be replayed for historical pages).
    const report = {};
    const wrapped = { check: (snap, ctx) => checker.check(snap, { ...ctx, report }) };
    const { signals } = await analyzeWithDomain(snapshot, { rdap: null, llm: wrapped, localSignals: local, client: "eval" });
    const imelda = { local: scoreOf(local), score: scoreOf([...local, ...signals]), layer4: report.outcome ?? "off", signals: [...local, ...signals].map((s) => s.id) };
    // Rebuilt layer 3 decides whether layer 4 is asked; the question and the model are the same.
    let rebuilt = { asked: false };
    const tier = tiers[id];
    if ((tier === "gate" || tier === "warn") && !isOfficialHost(hostOf(snapshot.url))) {
        const r = {};
        const ruleIds = signals.filter((s) => s.layer === 3).map((s) => s.id);
        const flag = await checker.check(snapshot, { official: false, score: 50, ruleIds, localSignals: local, client: "eval", report: r });
        rebuilt = { asked: true, outcome: r.outcome ?? null, flagged: !!flag, evidence: flag?.evidence ?? null };
    }
    appendFileSync(outPath, JSON.stringify({ id, tier, imelda, rebuilt }) + "\n");
}

const queue = [...todo];
let n = 0;
await Promise.all(
    Array.from({ length: 6 }, async () => {
        while (queue.length) {
            const id = queue.shift();
            try {
                await one(id);
            } catch (e) {
                appendFileSync(outPath, JSON.stringify({ id, tier: tiers[id], error: String(e).slice(0, 200) }) + "\n");
            }
            if (++n % 100 === 0) console.log(n, JSON.stringify(checker.stats().outcomes));
        }
    }),
);
console.log("done", JSON.stringify(checker.stats()));
