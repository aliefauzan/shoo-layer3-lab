"""Builds site/urls-500.csv: labelled URLs for the compare site, none from a site the model was trained on.

Usage: python3 -I site/make_urls.py out <feeds dir> site/urls-500.csv
  scam   today's public phishing feeds (reported URLs that still answer) + online gambling pages from held-out sites
  aman   legit pages from held-out sites: Indonesian sites, login/checkout pages, popular and small sites
"""
import csv
import json
import random
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SECOND_LEVEL = re.compile(r"^(co|go|ac|or|web|sch|my|biz|net|com|org|gov|edu|mil|desa|ponpes)\.[a-z]{2}$")


def site(url):
    p = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0].removeprefix("www.").split(".")
    return ".".join(p[-3:] if SECOND_LEVEL.match(".".join(p[-2:])) else p[-2:])


def alive(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"})
        with urllib.request.urlopen(req, timeout=8) as res:
            return res.status < 400 and len(res.read(3000)) > 300
    except Exception:
        return False


def main(out_dir, feeds, target):
    random.seed(5)
    rows, trained = [], set()
    for name in ("pages", "extra", "extra2"):
        for line in open(f"{out_dir}/{name}.jsonl", encoding="utf-8"):
            r = json.loads(line)
            if r["split"] == "train":
                trained.add(site(r.get("url") or r["host"]))
            rows.append(r)
    held = [r for r in rows if r["split"] in ("test", "dev") and (r.get("url") or "").startswith("http") and "web.archive" not in r["url"]]

    feed = []
    for f in sorted(Path(feeds).glob("*.txt")):
        feed += [(u.strip(), f.stem) for u in f.read_text(errors="ignore").splitlines() if re.match(r"^https?://[^\s]+$", u.strip())]
    random.shuffle(feed)
    seen, candidates = {}, []
    for url, src in feed:
        s = site(url)
        if s in trained or seen.get(s, 0) >= 1 or len(url) > 300:
            continue
        seen[s] = 1
        candidates.append((url, src))
    with ThreadPoolExecutor(32) as pool:
        live = [c for c, ok in zip(candidates, pool.map(lambda c: alive(c[0]), candidates)) if ok]
    print("feed urls", len(feed), "one per site", len(candidates), "still answering", len(live))

    out = [(u, "scam", f"feed:{src}") for u, src in live[:210]]
    out += [(r["url"], "scam", "judol:" + r["split"]) for r in held if r.get("source") == "judol"]
    n_scam = len(out)
    quota = {"hard_legit": 0.28, "legit_id": 0.28, "indo": 0.14, "legit_top": 0.15, "legit_tail": 0.15}
    need = 500 - n_scam
    for src, share in quota.items():
        pool_rows = [r for r in held if r.get("source") == src and r["split"] == "test" and r["label"] == 0]
        random.shuffle(pool_rows)
        used = set()
        for r in pool_rows:
            if len([o for o in out if o[2] == "legit:" + src]) >= round(need * share):
                break
            if site(r["url"]) in used:
                continue
            used.add(site(r["url"]))
            out.append((r["url"], "aman", "legit:" + src))
    random.shuffle(out)
    with open(target, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "label", "source"])
        w.writerows(out)
    print("written", len(out), "scam", n_scam, "aman", len(out) - n_scam)


if __name__ == "__main__":
    main(*sys.argv[1:4])
