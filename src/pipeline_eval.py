"""Full-pipeline comparison on held-out pages, from out/layer4.jsonl (src/layer4_run.mjs).

Usage: python3 -I src/pipeline_eval.py out [layer4 file]
  imelda    her pipeline as shipped: on-device layers 1-2, layer 3 rules, layer 4 behind her 30..79 gate
  rebuilt   rebuilt layer 3 tiers + the same layer 4:
            add     layer 4 can only raise: gate + flagged -> warn, gate + not flagged -> safe, warn stays warn
            settle  layer 4 also settles weak warnings: warn + "reasonable" -> safe
            strict  nothing passes without a second look: gate + not flagged still warns softly (counted as warned)
  rebuilt layer 3 + general scam-judge prompt (out/layer4_v5.jsonl):
            judge       gate/warn + "legit" -> safe, warn + "scam" -> block, everything else in gate/warn -> warn
            judge-keep  same, but a warn-tier page stays warned even when the judge says "legit"
Not simulated for either: live blocklist and domain age (historical pages).
"""
import json
import os
import sys
from collections import defaultdict


def main(out_dir, l4_name="layer4.jsonl"):
    rows = {}
    for name in ("pages", "extra", "extra2"):
        for line in open(f"{out_dir}/{name}.jsonl", encoding="utf-8"):
            r = json.loads(line)
            rows[r["id"]] = {"label": r["label"], "source": r.get("source", "scamweb"), "password": r["feats"]["passwordField"]}
    for line in open(f"{out_dir}/corpus.jsonl", encoding="utf-8"):
        r = json.loads(line)
        rows[r["id"]] = {"label": r["label"], "source": "corpus-" + ("handwritten" if r.get("synthetic") else "real"), "password": r["feats"]["passwordField"]}

    results = [json.loads(line) for line in open(f"{out_dir}/{l4_name}", encoding="utf-8")]
    judge = {}
    if os.path.exists(f"{out_dir}/layer4_v5.jsonl"):
        judge = {r["id"]: r for r in map(json.loads, open(f"{out_dir}/layer4_v5.jsonl", encoding="utf-8"))}
    errors = sum("error" in r for r in results)
    sets = defaultdict(list)
    for r in results:
        if "error" in r or r["id"] not in rows:
            continue
        meta = rows[r["id"]]
        kind = "scam" if meta["label"] else "benign"
        score = r["imelda"]["score"]
        t, l4 = r["tier"], r["rebuilt"]
        flagged, reasonable = bool(l4.get("flagged")), l4.get("outcome") == "reasonable"
        verdict = {
            "imelda": "block" if score >= 80 else "warn" if score >= 30 else "safe",
            "l3 only": "block" if t == "block" else "warn" if t == "warn" else "safe",
            "add": "block" if t == "block" else "warn" if t == "warn" or (t == "gate" and flagged) else "safe",
            "settle": "block" if t == "block" else "warn" if (t == "warn" and not reasonable) or (t == "gate" and flagged) else "safe",
            "strict": "block" if t == "block" else "warn" if t in ("warn", "gate") else "safe",
        }
        # General scam-judge prompt (src/layer4_v5.mjs). No answer or "unsure" never clears a page.
        j = judge.get(r["id"], {}).get("verdict") if t in ("gate", "warn") else None
        l4 = {**l4, "judge": j, "judged": t in ("gate", "warn")}
        if judge:
            verdict["judge"] = "block" if t == "block" or (t == "warn" and j == "scam") else "safe" if j == "legit" else "warn" if t in ("gate", "warn") else "safe"
            verdict["judge-keep"] = "block" if t == "block" or (t == "warn" and j == "scam") else "warn" if t == "warn" or (t == "gate" and j != "legit") else "safe"
        corpus = meta["source"].startswith("corpus")
        names = [f"{kind}: {meta['source']}"] + ([] if corpus else [f"{kind.upper()} all (test)"])
        if kind == "benign" and meta["password"] and not corpus:
            names.append("benign: has password form")
        for n in names:
            sets[n].append((verdict, l4))

    print(f"pages {len(results)}, errors {errors}")
    asked = [l4 for v in sets.values() for _, l4 in v if l4.get("asked")]
    variants = ("imelda", "l3 only", "add", "strict") + (("judge", "judge-keep") if judge else ())
    print(f"\n{'':44}" + "".join(f"{v:>22}" for v in variants))
    for kind, label in (("scam", "SCAM: blocked / warned-or-blocked / SAFE (missed)"), ("benign", "BENIGN: blocked / warned-or-blocked")):
        print(label)
        for name in sorted(sets, key=lambda n: (not n.isupper() and not n.startswith(kind.upper()), n)):
            if not name.lower().startswith(kind):
                continue
            items = sets[name]
            n = len(items)
            cells = []
            for v in variants:
                b = sum(x[0][v] == "block" for x in items)
                w = sum(x[0][v] in ("block", "warn") for x in items)
                cells.append(f"{100*b/n:5.1f} /{100*w/n:5.1f} /{100*(n-w)/n:5.1f}" if kind == "scam" else f"{100*b/n:8.1f} /{100*w/n:5.1f}  ")
            print(f"  {name[:36]:36} n={n:4} " + "".join(f"{c:>22}" for c in cells))
    # What the layer 4 model said when the rebuilt gate asked it.
    for kind in ("scam", "benign"):
        said = defaultdict(int)
        for name, items in sets.items():
            if name == f"{kind.upper()} all (test)":
                for _, l4 in items:
                    if l4.get("asked"):
                        said["flagged" if l4.get("flagged") else str(l4.get("outcome"))] += 1
        print(f"layer 4 answers on {kind} pages it was asked about: {dict(said)}")
        if judge:
            said = defaultdict(int)
            for _, l4 in sets.get(f"{kind.upper()} all (test)", []):
                if l4["judged"]:
                    said[str(l4["judge"])] += 1
            print(f"scam-judge answers on {kind} pages (gate + warn tier): {dict(said)}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
