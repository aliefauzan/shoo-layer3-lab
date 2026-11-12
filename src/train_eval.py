"""Train the rebuilt layer 3 (stacked model) and compare it with the current rules on held-out sites.

Usage: python src/train_eval.py out
Data: out/pages.jsonl (ScamWeb) + out/extra.jsonl (real phishing, Indonesian scam pages, legit pages incl. logins and
small sites), out/corpus.jsonl (Imelda's Indonesian eval corpus, test only), data/judol.csv captions (training only).

Model, all inputs from the PageSnapshot contract (url, title, text, forms):
  base 1  meaning   logistic regression on the mean bge-m3 chunk vector
  base 2  chunkmax  logistic regression per chunk, page = highest chunk
  base 3  gambling  gambling pages + Indonesian gambling captions against benign text, page = highest chunk
  base 4  words     logistic regression on word and character n-grams of title + text
  base 5  address   logistic regression on character n-grams of host + path
  meta    gradient-boosted trees on the 5 base scores + form facts + brand/address facts + current rule signals
Base scores for the meta model are out-of-fold (5-fold by site) on train, so the meta model never sees a score that
was fitted on the same page. Thresholds are picked on dev per language group and applied unchanged to test.
Tiers: block (dev benign <= 0.5%), warn (<= 2%), gate = send to layer 4 (<= 15%). Below the gate = passed as safe.
"""
import csv
import json
import os
import re
import sys
from collections import defaultdict

import joblib
import numpy as np
from scipy.sparse import hstack as sparse_hstack
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

RULE_SIGNALS = ["asks_password", "asks_otp", "asks_pin", "asks_ktp", "asks_cvv", "asks_card", "asks_selfie_ktp", "urgent_language", "brand_logo_offsite"]
LOGGED = {"textLen", "fields", "forms", "imageAlts", "hostLen", "pathLen"}
FEATS = [
    "textLen", "fields", "passwordField", "forms", "imageAlts", "formActionOffsite",
    "official", "brandsInTitle", "brandsInText", "brandOffsite", "brandTextOffsite", "brandInHost", "cheapTld", "sharedHost",
    "hostLen", "hostDigits", "hostHyphens", "subdomains", "pathLen", "pathDepth", "ipHost", "punycode", "urlKeywords",
]
ADDRESS_FEATS = {"official", "brandInHost", "cheapTld", "sharedHost", "hostLen", "hostDigits", "hostHyphens", "subdomains", "pathLen", "pathDepth", "ipHost", "punycode", "urlKeywords"}
GAMBLING = "Online Gambling Scam"
TIERS = (("block", 0.005), ("warn", 0.02), ("gate", 0.15))
SECOND_LEVEL = re.compile(r"^(co|go|ac|or|web|sch|my|biz|net|com|org|gov|edu|mil|desa|ponpes)\.[a-z]{2}$")
ID_WORDS = set("yang dan di untuk dengan dari ini itu pada adalah atau juga akan tidak kami anda daftar masuk situs terbaru hari lebih dalam ke oleh sebagai bisa dapat beranda resmi terpercaya hubungi promosi permainan bermain menang mudah togel gacor".split())


def site(host):
    p = host.removeprefix("www.").split(".")
    return ".".join(p[-3:] if SECOND_LEVEL.match(".".join(p[-2:])) else p[-2:])


def is_indonesian(row):
    """Share of common Indonesian words in the text. Cheap enough for a Worker; no model."""
    words = re.findall(r"[a-z]+", (row["title"] + " " + row["text"][:4000]).lower())
    return len(words) >= 15 and sum(w in ID_WORDS for w in words) / len(words) >= 0.04


def page_words(row):
    return row["title"] + " \n " + row["text"][:6000]


def page_address(row):
    path = re.sub(r"^https?://[^/]+", "", row.get("url") or "")
    return row["host"] + " " + path.split("?")[0][:120]


def side_matrix(rows, feats=FEATS):
    def value(r, k):
        v = float(r["feats"].get(k, 0))
        return np.log1p(v) if k in LOGGED else v

    f = [[value(r, k) for k in feats] for r in rows]
    rules = [[float(s in r["baseline"]["signals"]) for s in RULE_SIGNALS] for r in rows]
    indo = [[float(is_indonesian(r))] for r in rows]
    return np.hstack([np.array(f, dtype=np.float32), np.array(rules, dtype=np.float32), np.array(indo, dtype=np.float32)])


def load(*pairs):
    """Each pair is (rows .jsonl, chunk-vector prefix); several are stacked into one set."""
    rows, embs, idxs = [], [], []
    for pages_path, emb_prefix in pairs:
        part = [json.loads(line) for line in open(pages_path, encoding="utf-8")]
        embs.append(np.load(emb_prefix + ".npy"))
        idxs.append(np.load(emb_prefix + ".idx.npy") + len(rows))
        rows += part
    emb, idx = np.vstack(embs).astype(np.float32), np.concatenate(idxs)
    mean = np.zeros((len(rows), emb.shape[1]), dtype=np.float32)
    np.add.at(mean, idx, emb)
    mean /= np.bincount(idx, minlength=len(rows))[:, None]
    return rows, emb, idx, mean


def dense_lr():
    return make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=3000, class_weight="balanced"))


def page_max(chunk_scores, idx, n):
    out = np.zeros(n)
    np.maximum.at(out, idx, chunk_scores)
    return out


class Base:
    """The five base models, fitted on the pages selected by `mask`."""

    NAMES = ("meaning", "chunkmax", "gambling", "words", "address")

    def __init__(self, rows, emb, idx, mean, y, cap_x, cap_y, mask):
        chunk_mask = mask[idx]
        self.meaning = dense_lr().fit(mean[mask], y[mask])
        self.chunk = dense_lr().fit(emb[chunk_mask], y[idx][chunk_mask])
        gamble_page = np.array([r["scamType"] == GAMBLING for r in rows])
        pos, neg = chunk_mask & gamble_page[idx], chunk_mask & (y[idx] == 0)
        self.gamble = dense_lr().fit(np.vstack([emb[pos], emb[neg], cap_x]), np.concatenate([np.ones(pos.sum()), np.zeros(neg.sum()), cap_y]))
        train_rows = [r for r, m in zip(rows, mask) if m]
        self.word_vec = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=3, max_features=150_000, sublinear_tf=True, lowercase=True)
        self.char_vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=5, max_features=200_000, sublinear_tf=True, lowercase=True)
        x = sparse_hstack([self.word_vec.fit_transform(map(page_words, train_rows)), self.char_vec.fit_transform(map(page_words, train_rows))]).tocsr()
        self.words = LogisticRegression(C=3.0, max_iter=2000, class_weight="balanced", solver="liblinear").fit(x, y[mask])
        self.addr_vec = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), min_df=3, max_features=80_000, sublinear_tf=True, lowercase=True)
        self.address = LogisticRegression(C=1.0, max_iter=2000, class_weight="balanced", solver="liblinear").fit(self.addr_vec.fit_transform(map(page_address, train_rows)), y[mask])

    def score(self, rows, emb, idx, mean):
        n = len(rows)
        text = sparse_hstack([self.word_vec.transform(map(page_words, rows)), self.char_vec.transform(map(page_words, rows))]).tocsr()
        return np.column_stack([
            self.meaning.predict_proba(mean)[:, 1],
            page_max(self.chunk.predict_proba(emb)[:, 1], idx, n),
            page_max(self.gamble.predict_proba(emb)[:, 1], idx, n),
            self.words.predict_proba(text)[:, 1],
            self.address.predict_proba(self.addr_vec.transform(map(page_address, rows)))[:, 1],
        ])


def fit_base(rows, emb, idx, mean, y, cap_x, cap_y, tr):
    # Base scores: out-of-fold on train (5-fold by site), full-train models for dev, test and the corpus.
    groups = np.array([site(r["host"]) for r in rows])
    base_scores = np.zeros((len(rows), len(Base.NAMES)))
    train_idx = np.where(tr)[0]
    for k, (a, b) in enumerate(GroupKFold(n_splits=5).split(train_idx, y[train_idx], groups[train_idx])):
        fit_mask = np.zeros(len(rows), dtype=bool)
        fit_mask[train_idx[a]] = True
        held = train_idx[b]
        chunk_held = np.isin(idx, held)
        base_scores[held] = Base(rows, emb, idx, mean, y, cap_x, cap_y, fit_mask).score([rows[i] for i in held], emb[chunk_held], np.searchsorted(held, idx[chunk_held]), mean[held])
        print(f"  fold {k + 1}/5 done", flush=True)
    base = Base(rows, emb, idx, mean, y, cap_x, cap_y, tr)
    rest = np.where(~tr)[0]
    chunk_rest = np.isin(idx, rest)
    base_scores[rest] = base.score([rows[i] for i in rest], emb[chunk_rest], np.searchsorted(rest, idx[chunk_rest]), mean[rest])

    return base_scores, base


def meta_model():
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.06, max_leaf_nodes=15, l2_regularization=1.0, class_weight="balanced", random_state=0)


def threshold_at_fpr(scores, fpr):
    benign = np.sort(scores)[::-1]
    return float(benign[min(len(benign) - 1, int(np.floor(fpr * len(benign))))]) + 1e-12


def main(out_dir):
    rows, emb, idx, mean = load((f"{out_dir}/pages.jsonl", f"{out_dir}/emb"), (f"{out_dir}/extra.jsonl", f"{out_dir}/extra_emb"), (f"{out_dir}/extra2.jsonl", f"{out_dir}/extra2_emb"))
    y = np.array([r["label"] for r in rows])
    split = np.array([r["split"] for r in rows])
    source = np.array([r.get("source", "scamweb") for r in rows])
    # Reported-phishing URLs fetched today mostly serve unrelated pages now (hotel sites, block notices): unusable labels.
    split[source == "indo_phish"] = "drop"
    tr, dv, te = split == "train", split == "dev", split == "test"
    indo = np.array([is_indonesian(r) for r in rows])
    official = np.array([bool(r["feats"].get("official")) for r in rows])
    posts = [p for p in csv.DictReader(open("data/judol.csv", encoding="utf-8", errors="replace")) if len(p["text_caption"].strip()) >= 20]
    cap_x, cap_y = np.load(f"{out_dir}/judol_emb.npy"), np.array([int(float(p["label"])) for p in posts])
    print(f"pages {len(rows)}: train {tr.sum()} dev {dv.sum()} test {te.sum()} | scam {y.sum()} benign {(y == 0).sum()} | detected Indonesian {indo.sum()}", flush=True)

    cache = f"{out_dir}/base_cache.npz"
    crows, cemb, cidx, cmean = load((f"{out_dir}/corpus.jsonl", f"{out_dir}/corpus_emb"))
    if os.path.exists(cache) and len(np.load(cache)["base_scores"]) == len(rows) and "--refit" not in sys.argv:
        saved = np.load(cache)  # meta-only run
        base, base_scores, cbase, noisy = None, saved["base_scores"], saved["cbase"], saved["noisy"]
        split[noisy] = "drop"
        tr = split == "train"
    else:
        # Pass 1 finds training pages whose label looks wrong: "scam" pages that every text and address model, fitted
        # without them, calls clearly benign (dead phishing URLs now showing a normal site), and the reverse for benign.
        first, _ = fit_base(rows, emb, idx, mean, y, cap_x, cap_y, tr)
        noisy = tr & (((y == 1) & (first[:, [0, 3, 4]].max(axis=1) < 0.2)) | ((y == 0) & (first[:, [0, 3]].min(axis=1) > 0.97)))
        split[noisy] = "drop"
        tr = split == "train"
        base_scores, base = fit_base(rows, emb, idx, mean, y, cap_x, cap_y, tr)
        cbase = base.score(crows, cemb, cidx, cmean)
        np.savez(cache, base_scores=base_scores, cbase=cbase, noisy=noisy)
    print("suspected wrong labels removed from training: " + ", ".join(f"{src} scam {int((noisy & (source == src) & (y == 1)).sum())} benign {int((noisy & (source == src) & (y == 0)).sum())}" for src in sorted(set(source[noisy]))), flush=True)

    side = side_matrix(rows)
    no_addr = [i for i, k in enumerate(FEATS) if k not in ADDRESS_FEATS] + list(range(len(FEATS), side.shape[1]))
    # Two meta models. One sees the address (strong on throwaway domains), one does not (a hijacked school or
    # government site has a trustworthy address but scam content). The final score is their average.
    variants = {
        "with_address": (np.hstack([base_scores, side]), lambda b, s: np.hstack([b, s])),
        "no_address": (np.hstack([base_scores[:, :4], side[:, no_addr]]), lambda b, s: np.hstack([b[:, :4], s[:, no_addr]])),
    }
    metas, scores = {}, {}
    for name, (x, _) in variants.items():
        metas[name] = meta_model().fit(x[tr], y[tr])
        scores[name] = metas[name].predict_proba(x)[:, 1]
    scores = {"final": (scores["with_address"] + scores["no_address"]) / 2, **scores}
    rules_score = np.array([r["baseline"]["score"] for r in rows], dtype=np.float32)

    # Thresholds per language group from that group's benign dev pages.
    thresholds = {name: {g: {tier: threshold_at_fpr(s[dv & (y == 0) & m], fpr) for tier, fpr in TIERS} for g, m in (("indonesian", indo), ("other", ~indo))} for name, s in scores.items()}

    def tiers(name, s, is_indo, is_official):
        out = {}
        for tier, _ in TIERS:
            limit = np.where(is_indo, thresholds[name]["indonesian"][tier], thresholds[name]["other"][tier])
            out[tier] = (s >= limit) & ~is_official  # official hosts are exempt, as in the current layer 3
        return out

    flags = {name: tiers(name, s, indo, official) for name, s in scores.items()}
    np.savez(f"{out_dir}/scores.npz", final=scores["final"], no_address=scores["no_address"], y=y, split=split, source=source, indo=indo, official=official, rules=rules_score)
    flags["rules"] = {"block": rules_score >= 80, "warn": rules_score >= 30, "gate": rules_score >= 30}

    def table(mask):
        n = int(mask.sum())
        return {"n": n, **{name: {tier: int(f[tier][mask].sum()) for tier in ("block", "warn", "gate")} for name, f in flags.items()}}

    labelled_indo = np.array(["Indonesian" in (r.get("language") or "") for r in rows])
    sets = {
        "SCAM all (test)": te & (y == 1),
        "scam: ScamWeb": te & (y == 1) & (source == "scamweb"),
        "scam: real phishing (CC0 set)": te & (source == "phish"),
        "scam: Indonesia-targeted phishing": te & (source == "indo_phish"),
        "scam: Indonesian phishing (real)": te & (source == "phish_id"),
        "scam: Indonesian judol (search)": te & (source == "judol"),
        "scam: ScamWeb Indonesian": te & (y == 1) & (source == "scamweb") & labelled_indo,
        "scam: ScamWeb gambling": te & (y == 1) & (source == "scamweb") & np.array([r["scamType"] == GAMBLING for r in rows]),
        "scam: detected Indonesian": te & (y == 1) & indo,
        "BENIGN all (test)": te & (y == 0),
        "benign: ScamWeb": te & (y == 0) & (source == "scamweb"),
        "benign: popular sites": te & (source == "legit_top"),
        "benign: small sites": te & (source == "legit_tail"),
        "benign: Indonesian sites": te & (y == 0) & np.isin(source, ["legit_id", "indo"]),
        "benign: hard Indonesian (scam news, help, KYC)": te & (source == "hard_legit"),
        "benign: has password form": te & (y == 0) & np.array([r["feats"]["passwordField"] for r in rows]),
        "benign: detected Indonesian": te & (y == 0) & indo,
    }
    report = {"thresholds": thresholds, "sets": {k: table(m) for k, m in sets.items()}}
    report["auc_test"] = {"rules": float(roc_auc_score(y[te], rules_score[te])), **{n: float(roc_auc_score(y[te], s[te])) for n, s in scores.items()}, **{f"base_{n}": float(roc_auc_score(y[te], base_scores[te, i])) for i, n in enumerate(Base.NAMES)}}

    # Imelda's Indonesian eval corpus: never trained on. Its scam side is mostly hand-written to exercise the rules.
    cy = np.array([r["label"] for r in crows])
    cindo = np.array([is_indonesian(r) for r in crows])
    cside = side_matrix(crows)
    crules = np.array([r["baseline"]["score"] for r in crows], dtype=np.float32)
    cofficial = np.array([bool(r["feats"].get("official")) for r in crows])
    cscores = {name: metas[name].predict_proba(build(cbase, cside))[:, 1] for name, (_, build) in variants.items()}
    cscores["final"] = (cscores["with_address"] + cscores["no_address"]) / 2
    cflags = {name: tiers(name, cscores[name], cindo, cofficial) for name in cscores}
    np.savez(f"{out_dir}/corpus_scores.npz", final=cscores["final"], y=cy, official=cofficial, indo=cindo)
    cflags["rules"] = {"block": crules >= 80, "warn": crules >= 30, "gate": crules >= 30}
    report["corpus"] = {kind: {"n": int(m.sum()), **{name: {tier: int(f[tier][m].sum()) for tier in ("block", "warn", "gate")} for name, f in cflags.items()}} for kind, m in (("scam", cy == 1), ("legit", cy == 0))}
    report["corpus"]["legit_warned_by_final"] = [r["id"] for r, w in zip(crows, cflags["final"]["warn"]) if w and r["label"] == 0]
    report["corpus"]["scam_below_gate_final"] = [r["id"] for r, w in zip(crows, cflags["final"]["gate"]) if not w and r["label"] == 1]

    def tier_names(f, n):
        return ["block" if f["block"][i] else "warn" if f["warn"][i] else "gate" if f["gate"][i] else "safe" for i in range(n)]

    tier_of = dict(zip([r["id"] for r in rows], tier_names(flags["final"], len(rows))))
    json.dump({**{i: t for i, t, m in zip(tier_of, tier_of.values(), te) if m}, **dict(zip([r["id"] for r in crows], tier_names(cflags["final"], len(crows))))}, open(f"{out_dir}/tiers.json", "w"))

    missed = defaultdict(list)
    for i in np.where(te & (y == 1) & ~flags["final"]["gate"])[0]:
        missed[source[i]].append({"host": rows[i]["host"], "title": rows[i]["title"][:70], "type": rows[i]["scamType"], "score": round(float(scores["final"][i]), 4)})
    report["scam_below_gate_final"] = missed
    json.dump(report, open(f"{out_dir}/report.json", "w"), indent=1, ensure_ascii=False)
    # Written and read only on this machine (src/analyze.py); never load a joblib file from elsewhere.
    if base is not None:
        joblib.dump({"base": base, "feats": FEATS}, f"{out_dir}/layer3_base.joblib")
    joblib.dump({"metas": metas, "no_address_columns": no_addr, "thresholds": thresholds["final"], "feats": FEATS}, f"{out_dir}/layer3_meta.joblib")

    print("AUC on test: " + "  ".join(f"{k} {v:.3f}" for k, v in report["auc_test"].items()))

    def show(title, t, benign):
        n = t["n"]
        if not n:
            return
        cells = []
        for name in ("rules", "final"):
            f = t[name]
            if benign:
                cells.append(f"{name}: warn {100 * f['warn'] / n:4.1f}% gate {100 * f['gate'] / n:4.1f}%")
            else:
                cells.append(f"{name}: block {100 * f['block'] / n:4.1f}% warn {100 * f['warn'] / n:4.1f}% gate {100 * f['gate'] / n:5.1f}% SAFE {100 * (n - f['gate']) / n:4.1f}%")
        print(f"  {title[:34]:34} n={n:5} | " + " | ".join(cells))

    print("\nscam pages: share blocked / warned or blocked / reaching layer 4 gate / passed as SAFE")
    for k, t in report["sets"].items():
        if "scam" in k.lower():
            show(k, t, False)
    print("\nbenign pages: share warned / sent to layer 4")
    for k, t in report["sets"].items():
        if "benign" in k.lower():
            show(k, t, True)
    c = report["corpus"]
    print("\nImelda's Indonesian corpus:")
    show("corpus scam", c["scam"], False)
    show("corpus legit", c["legit"], True)
    print("  legit warned by final:", c["legit_warned_by_final"])
    real = np.array([not r.get("synthetic") for r in crows])
    print(f"  real (not hand-written) legit pages warned: {int((cflags['final']['warn'] & (cy == 0) & real).sum())}/{int(((cy == 0) & real).sum())}, sent to layer 4: {int((cflags['final']['gate'] & (cy == 0) & real).sum())}")
    print("  scam below gate (final):", c["scam_below_gate_final"])


if __name__ == "__main__":
    main(sys.argv[1])
