"""Run the rebuilt layer 3 on one page and show it next to the current rules.

  python analyze.py --id scam_13041                       a page from out/pages.jsonl, out/extra.jsonl or out/corpus.jsonl
  python analyze.py --html saved.html --url https://x/    any saved page (static HTML, never fetched here)
Run from src/.

Output: tier (safe / gate = ask layer 4 / warn / block), the five base scores, and the page chunk that scored
highest as evidence. The evidence is always an exact piece of the page text, as the /analyze contract requires.
"""
import argparse
import json
import subprocess
from pathlib import Path

import joblib
import numpy as np

from embed import chunks, encode, load_model
from train_eval import Base, is_indonesian, side_matrix  # noqa: F401  (Base is needed to unpickle the bundle)

ROOT = Path(__file__).resolve().parent.parent
SHOO_AI = Path.home() / "epel/Final-CH7/ai"


def load_row(args):
    if args.id:
        for name in ("pages", "extra", "corpus"):
            for line in open(ROOT / f"out/{name}.jsonl", encoding="utf-8"):
                row = json.loads(line)
                if row["id"] == args.id:
                    return row
        raise SystemExit(f"no page with id {args.id}")
    out = subprocess.run(["node", str(ROOT / "src/build_snapshots.mjs"), str(SHOO_AI), "--one", args.html, args.url], capture_output=True, text=True, check=True)
    return {**json.loads(out.stdout), "url": args.url}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id")
    ap.add_argument("--html")
    ap.add_argument("--url")
    args = ap.parse_args()
    row = load_row(args)
    # Safe to unpickle: both files are written by src/train_eval.py on this machine, never downloaded.
    base = joblib.load(ROOT / "out/layer3_base.joblib")["base"]
    meta = joblib.load(ROOT / "out/layer3_meta.joblib")

    parts = chunks(row)
    emb = encode(load_model(), parts)
    idx = np.zeros(len(parts), dtype=np.int32)
    scores = base.score([row], emb, idx, emb.mean(axis=0, keepdims=True))
    side = side_matrix([row])
    with_address = float(meta["metas"]["with_address"].predict_proba(np.hstack([scores, side]))[0, 1])
    no_address = float(meta["metas"]["no_address"].predict_proba(np.hstack([scores[:, :4], side[:, meta["no_address_columns"]]]))[0, 1])
    final = (with_address + no_address) / 2
    group = "indonesian" if is_indonesian(row) else "other"
    limits = meta["thresholds"][group]
    official = bool(row["feats"].get("official"))
    tier = "safe" if official else next((t for t in ("block", "warn", "gate") if final >= limits[t]), "safe")

    evidence = None
    if tier != "safe":
        per_chunk = np.maximum(base.chunk.predict_proba(emb)[:, 1], base.gamble.predict_proba(emb)[:, 1])
        evidence = parts[int(np.argmax(per_chunk))].split("\n", 1)[-1][:240]  # that chunk's page text, without the title

    rules_score = row["baseline"]["score"]
    print(json.dumps({
        "host": row["host"],
        "title": row["title"][:100],
        "current_rules": {"score": rules_score, "tier": "block" if rules_score >= 80 else "warn" if rules_score >= 30 else "safe", "signals": row["baseline"]["signals"]},
        "rebuilt_layer3": {
            "tier": tier,
            "score": round(final, 4),
            "limits": {k: round(v, 4) for k, v in limits.items()},
            "language_group": group,
            "official_host": official,
            "base_scores": {n: round(float(v), 3) for n, v in zip(Base.NAMES, scores[0])},
            "with_address": round(with_address, 4),
            "no_address": round(no_address, 4),
            "evidence": evidence,
        },
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
