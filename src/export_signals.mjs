// Full signal objects (layer, points, evidence) from the shipped layers 1-3 for the pages in a sample file,
// plus what the snapshot holds. Usage: node src/export_signals.mjs <ai/> out <sample.json> <result.json>
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
const [repoAi, outDir, samplePath, resultPath] = process.argv.slice(2);
const { loadDetector, normalizeSnapshot } = await import(join(repoAi, "eval/lib.mjs"));
const { analyzeWithDomain } = await import(join(repoAi, "src/analyze.js"));
const want = new Set(JSON.parse(readFileSync(samplePath, "utf8")).map((r) => r.id));
const detector = loadDetector();
const result = {};
for (const f of readdirSync(outDir).filter((x) => x.endsWith(".snapshots.jsonl"))) {
    for (const line of readFileSync(join(outDir, f), "utf8").split("\n")) {
        if (!line) continue;
        const { id, snapshot } = JSON.parse(line);
        if (!want.has(id)) continue;
        const snap = normalizeSnapshot(snapshot);
        const local = (await detector.analyzeLocal(snap)).filter((s) => [1, 2].includes(s.layer));
        const { signals } = await analyzeWithDomain(snap, { rdap: null, llm: null, localSignals: local, client: "eval" });
        const fields = (snap.forms || []).flatMap((x) => x.fields || []);
        result[id] = {
            signals: [...local, ...signals].map((s) => Object.fromEntries(Object.entries(s).map(([k, v]) => [k, typeof v === "string" ? v.slice(0, 200) : v]))),
            read: { textLen: (snap.text || "").length, forms: (snap.forms || []).length, fields: fields.map((x) => x.type || x.name || "field").slice(0, 8), lang: snap.lang || null, imageAlts: (snap.imageAlts || []).length },
        };
    }
}
writeFileSync(resultPath, JSON.stringify(result));
console.log("pages", Object.keys(result).length);
