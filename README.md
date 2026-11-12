# shoo-layer3-lab

Experiment: a rebuilt Shoo layer 3 (structure signals + text meaning) compared with the current layer 3 rules in
`Final-CH7/ai`. Nothing here ships. The Shoo repo is only read (its parser and rules are imported, never edited).

## Run

```bash
A=~/epel/Final-CH7/ai
node src/build_snapshots.mjs $A data/scamweb out/pages.jsonl                         # ScamWeb snapshots + current rule result
node src/build_snapshots.mjs $A --corpus out/corpus.jsonl                            # Imelda's Indonesian eval corpus
python3 -I src/fetch_pages.py sites data/legit_top data/lists/sites_top.txt          # legit pages: home + login/checkout + one more
python3 -I src/fetch_pages.py urls data/judol data/lists/judol_urls.txt              # reported scam URLs, static GET only
uv run --no-project --with duckdb python -I src/sample_phish.py data/phish 6         # real phishing pages (CC0 set)
node src/build_snapshots.mjs $A --dir out/src_phish.jsonl data/phish 1 phish -       # one per source: <dir> <label> <prefix> <language|->
python3 -I src/assemble.py out                                                       # clean + merge sources -> out/extra.jsonl
uv run python src/embed.py out/pages.jsonl out/emb                                   # bge-m3 chunk vectors
uv run python src/embed.py out/extra.jsonl out/extra_emb
uv run python src/embed.py out/corpus.jsonl out/corpus_emb
uv run python src/train_eval.py out --refit                                          # full fit (about 25 min); without --refit: meta only
cd src && uv run python analyze.py --id scam_18544                                   # one page, both systems, with evidence
```

## Data (not committed)

| Source | Label | Pages used |
|---|---|---|
| `kirco567/ScamWeb-Dataset` (CC BY 4.0) | scam + benign | 3,041 + 2,227 |
| `nyuuzyou/phishing-snapshots` (CC0), takedown pages removed | scam | 3,489 |
| Indonesian judol pages found by search, gambling wording required | scam | 90 |
| Tranco top sites: home, login/checkout, one more page | benign | 1,714 |
| Tranco long-tail small sites | benign | 2,500 |
| Indonesian `.id` sites and 173 hand-listed Indonesian sites | benign | 1,610 |
| `0xRafie/indonesian-gambling-multimodal-dataset` captions | training only | 4,892 |
| Imelda's eval corpus (47 scam, 45 hand-written; 39 legit) | test only | 86 |

Dropped: Indonesia-targeted URLs from Phishing.Database fetched live. They now serve unrelated pages, so the labels
are wrong. Scam pages are hostile: only saved HTML is parsed as text; never open one in a browser.

## Design

Input is the PageSnapshot contract only (url, title, text, forms).

1. Five base models: page meaning (mean bge-m3 chunk vector), highest-scoring chunk, gambling head, word and
   character patterns of the text, character patterns of the address.
2. Facts: form fields, brand named on a non-official host, cheap TLD, shared host, current rule signals.
3. Two boosted-tree combiners, one with the address and one without, averaged. A hijacked school or government
   site has a trustworthy address but scam content; the no-address model covers that case.
4. Official hosts are exempt, as in the current layer 3.
5. Tiers, per language group, set on benign dev pages: block (<= 0.5% of benign), warn (<= 2%), gate = send to
   layer 4 (<= 15%). Below the gate the page passes as safe.

Training pages whose label looks wrong are removed (19 of 10,357): "scam" pages that every model fitted without
them calls clearly benign. Test pages are never removed.

## Result (held-out sites, 2,149 pages: 790 scam, 1,359 benign)

| | Current rules | Rebuilt layer 3 |
|---|---|---|
| Scam blocked | 0.0% | 47.7% |
| Scam warned or blocked | 0.8% | 79.5% |
| Scam reaching layer 4 or higher | 0.8% | 95.8% |
| Scam passed as safe | 99.2% | 4.2% |
| Benign warned | 0.7% | 2.9% |
| Benign sent to layer 4 | 0.7% | 16.4% |

Indonesian: judol pages (23) passed as safe 0; ScamWeb Indonesian (3) 0; Imelda corpus scam (47) 0, against 16 for
the current rules. Real legit pages in Imelda's corpus warned: 0 of 29. Hand-written legit trap pages warned: 7 of 10.

Audit of the 32 held-out scam pages passed as safe (judged from their text, with the earlier with-address model):
5 real misses, 8 are the genuine legit site (wrong label), 19 cannot be told from an ordinary site by content.

## Known limits

- "Current rules" is layer 3 alone: no blocklist, no URL-shape signals, no domain age, no LLM.
- Hand-written legit traps (legit KYC, a news article warning about scams, shop checkout with card fields) are
  still warned. Content alone cannot separate them; layer 4 or domain age must.
- Clone shops that copy a real shop word for word look legit by content. Domain age is the signal for those.
- Indonesian scam test data is small: 23 judol pages, 3 ScamWeb pages, 47 mostly hand-written corpus pages. No real
  Indonesian phishing set exists yet; production reports are the only source.
- Benign pages sent to layer 4: 16.4% overall, 28.8% for pages with a password form. That is LLM cost and latency.
- Hijacked sites often show the scam only to search engines; a plain fetch sees the clean site.
- Runs in Python on a Mac. For the Worker: embedding via Workers AI, linear heads and trees as weight tables.

## Update, 12 Nov 2026 (supersedes the numbers above)

Data: 15,876 pages (train 10,995 / dev 2,431 / test 2,402; test = 828 scam, 1,574 benign), split by site. New sources:
165 real Indonesian phishing pages (`phish_id`, from `nyuuzyou/phishing-snapshots`) and 992 hard legit pages (`hard_legit`).

Full pipeline on held-out sites (`src/layer4_run.mjs`, `src/layer4_v5.mjs`, `src/pipeline_eval.py`). Not simulated for
either side: live blocklist and domain age, because the test pages are historical.

| Pipeline | Scam passed as safe | Benign warned or blocked |
|---|---|---|
| Shipped: layers 1-2, layer 3 rules, layer 4 `reality-check.v4` | 96.6% | 1.1% |
| Rebuilt layer 3 alone (block + warn tiers) | 17.9% | 2.8% |
| Rebuilt layer 3, gate tier also warns (`strict`) | 3.9% | 15.9% |
| Rebuilt layer 3 + `scam-judge.v1`, judge "legit" clears gate and warn (`judge`) | 23.3% | 2.7% |
| Rebuilt layer 3 + `scam-judge.v1`, judge "legit" clears gate only (`judge-keep`) | 13.3% | 4.7% |

Findings:

- `reality-check.v4` judges only what a form asks for. Asked about 373 scam pages it answered "reasonable" on 353.
  It cannot be the second opinion for scams without a form (shops, gambling, investment, giveaways).
- `scam-judge.v1` (general prompt, same model, written once, not tuned on test) raised blocked scam from 51.1% to 63.5%
  and called 12 of 245 benign pages scam. It called 161 scam-labelled pages legit. Of those, 36 are sites in the
  Tranco top 1M (label most likely wrong, e.g. decathlon.co.uk) and 11 are dead or placeholder pages. The rest are
  mostly shop pages that read like a real shop: content alone cannot tell them apart. That needs domain age and the
  blocklist, which run live and are not measured here.
- Indonesian: real Indonesian phishing (38 test pages) 0 passed as safe under `strict`, 2.6% under `judge-keep`;
  online gambling (23) 0 passed as safe under both.
- Hand-written trap pages from the eval corpus (10): still 6-7 warned. Real legit corpus pages (29): 0 warned.
- Cost of `scam-judge.v1`: 637 calls, 1.11M tokens in, 0.33M out, median 1.5 s, p95 2.6 s. Asked for 26.5% of all
  test pages (15.6% of benign).
- `ai/eval/tools/snapshot.mjs` hang: the email mask in `ai/eval/tools/patterns.mjs:14` is quadratic on long runs
  without `@`. Reproducers: `data/phish/00590.html.gz`, `data/phish/02224.html.gz`. This lab cuts unfinished
  `<script>`/`<style>` tails and limits each page to 4 s.

## Compare site (`site/`)

Paste URLs, both pipelines answer side by side, with a screenshot and the evidence per layer.

```bash
cd site && npm install && cd ..
uv run python site/server.py     # then open http://127.0.0.1:8799
```

- `site/reader.mjs` opens each URL once in headless Chromium (phone size, fresh profile), takes a screenshot, and runs the
  shipped pipeline from the Shoo repo: layers 1-2, layer 3 rules, live domain age, layer 4.
- `site/server.py` runs the rebuilt layer 3 (needs `out/layer3_base.joblib` and `out/layer3_meta.joblib` from
  `src/train_eval.py`) and the `scam-judge.v1` prompt. It listens on 127.0.0.1 only.
- Both read the LLM settings from `~/epel/Final-CH7/ai/.dev.vars` into memory. No key is stored in this repo.
- `site/urls-500.csv`: 500 labelled URLs (reported phishing from public feeds, online gambling, legit sites), none from a
  site the model was trained on. The scam URLs are live: do not open them in a normal browser.
- `site/compare.html` is the earlier static mock (`site/build.py` builds it from `template.html` and the sample files).
- `data/` and `out/` (datasets, vectors, trained models) are not in git.
