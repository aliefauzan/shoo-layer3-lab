"""Per-page evidence from the rebuilt layer 3 for the pages in a sample file (for the compare site).

Usage (from the project root): uv run python src/export_evidence.py out <sample.json> <result.json>
Per page: final score and tier limits, the five base scores, the page chunk that scored highest, the words that
pushed the word model most toward scam, and the address/form facts that are set.
"""
import json
import sys

import joblib
import numpy as np

from embed import chunks
from train_eval import Base, is_indonesian, load, page_words  # noqa: F401  (Base is needed to unpickle the bundle)

FACTS = ["official", "brandOffsite", "brandTextOffsite", "brandInHost", "cheapTld", "sharedHost", "ipHost", "punycode", "urlKeywords", "passwordField", "formActionOffsite"]


def main(out_dir, sample_path, result_path):
    want = {r["id"] for r in json.load(open(sample_path, encoding="utf-8"))}
    rows, emb, idx, mean = load((f"{out_dir}/pages.jsonl", f"{out_dir}/emb"), (f"{out_dir}/extra.jsonl", f"{out_dir}/extra_emb"), (f"{out_dir}/extra2.jsonl", f"{out_dir}/extra2_emb"))
    saved = np.load(f"{out_dir}/scores.npz", allow_pickle=True)
    assert len(rows) == len(saved["final"]) and all(int(r["label"]) == int(y) for r, y in zip(rows, saved["y"])), "row order differs from the training run"
    # Safe to unpickle: both files are written by src/train_eval.py on this machine, never downloaded.
    base = joblib.load(f"{out_dir}/layer3_base.joblib")["base"]
    meta = joblib.load(f"{out_dir}/layer3_meta.joblib")
    n_words = len(base.word_vec.vocabulary_)
    terms = base.word_vec.get_feature_names_out()
    coef = base.words.coef_[0][:n_words]
    result = {}
    for i, row in enumerate(rows):
        if row["id"] not in want:
            continue
        mine = emb[idx == i]
        scores = base.score([row], mine, np.zeros(len(mine), dtype=np.int32), mean[i : i + 1])[0]
        chunk_p, gamble_p = base.chunk.predict_proba(mine)[:, 1], base.gamble.predict_proba(mine)[:, 1]
        best = int(np.argmax(np.maximum(chunk_p, gamble_p)))
        parts = chunks(row)
        pull = base.word_vec.transform([page_words(row)]).multiply(coef).tocoo()
        top = sorted(zip(pull.data, pull.col), reverse=True)[:6]
        limits = meta["thresholds"]["indonesian" if is_indonesian(row) else "other"]
        result[row["id"]] = {
            "final": round(float(saved["final"][i]), 4),
            "limits": {k: round(float(v), 4) for k, v in limits.items()},
            "indo": bool(is_indonesian(row)),
            "base": {n: round(float(v), 3) for n, v in zip(Base.NAMES, scores)},
            "chunk": {"text": parts[best].split("\n", 1)[-1][:260] if best < len(parts) else "", "kind": "gambling" if gamble_p[best] > chunk_p[best] else "scam", "score": round(float(max(chunk_p[best], gamble_p[best])), 3)},
            "words": [terms[c] for v, c in top if v > 0],
            "facts": [k for k in FACTS if row["feats"].get(k)],
        }
    json.dump(result, open(result_path, "w", encoding="utf-8"), ensure_ascii=False)
    print("pages", len(result))


if __name__ == "__main__":
    main(*sys.argv[1:4])
