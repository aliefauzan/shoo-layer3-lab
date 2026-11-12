"""Merge the extra page sources into out/extra.jsonl (ScamWeb stays in out/pages.jsonl).

Usage: python3 -I src/assemble.py out
Cleans labels that a static fetch cannot guarantee:
  - reported phishing URLs that now serve a takedown, parking or error page are dropped (not phishing content any more)
  - "legit" pages from the site ranking that are gambling or adult sites are dropped (ranking popularity is not legitimacy)
  - search-found gambling pages must show gambling wording
Exact duplicate texts are dropped, and each source is capped so no source dominates.
"""
import hashlib
import json
import os
import random
import re
import sys

DEAD = re.compile(
    r"blocked by bitly|suspected phishing|deceptive site|account (has been )?suspended|page not found|404|not found|"
    r"domain (is )?for sale|buy this domain|can.t be reached|access denied|attention required|just a moment|"
    r"web server is down|coming soon|under construction|index of /|default web page|apache2|welcome to nginx|parked|"
    r"hosting|cpanel|forbidden|error 5\d\d|bad gateway|website is no longer|has been removed|temporarily unavailable|captcha",
    re.I,
)
GAMBLING = re.compile(r"slot|gacor|togel|judi|casino|kasino|maxwin|sbobet|toto|jackpot|bandar|poker|taruhan|betting|sportsbook|rtp", re.I)
ADULT = re.compile(r"porn|xxx|sex|bokep|escort|hentai|nude", re.I)

# source file, cap, keep(row) -> bool
SOURCES = [
    ("indo", 10_000, lambda r: True),
    ("legit_id", 2_500, lambda r: not GAMBLING.search(r["title"]) and not ADULT.search(r["title"] + r["host"])),
    ("legit_top", 2_500, lambda r: not GAMBLING.search(r["title"] + r["host"]) and not ADULT.search(r["title"] + r["host"])),
    ("legit_tail", 2_500, lambda r: not GAMBLING.search(r["title"] + r["host"]) and not ADULT.search(r["title"] + r["host"])),
    ("phish", 4_000, lambda r: not r["feats"]["official"] and not DEAD.search(r["title"] + " " + r["text"][:400])),
    ("indo_phish", 10_000, lambda r: not r["feats"]["official"] and not DEAD.search(r["title"] + " " + r["text"][:400])),
    ("judol", 10_000, lambda r: not r["feats"]["official"] and len(GAMBLING.findall(r["title"] + " " + r["text"][:3000])) >= 3),
]
# Second batch, written to its own file so the first batch (already embedded) stays untouched.
SOURCES_2 = [
    ("phish_id", 10_000, lambda r: not r["feats"]["official"] and not DEAD.search(r["title"] + " " + r["text"][:400])),
    ("hard_legit", 10_000, lambda r: not ADULT.search(r["title"] + r["host"])),
]


def main(out_dir, batch="extra"):
    random.seed(11)
    seen, out = set(), []
    if batch != "extra":  # do not repeat a page the first batch already has
        seen = {hashlib.sha1((r["title"] + r["text"][:2000]).encode()).hexdigest() for r in map(json.loads, open(f"{out_dir}/extra.jsonl", encoding="utf-8"))}
    for name, cap, keep in SOURCES if batch == "extra" else SOURCES_2:
        path = f"{out_dir}/src_{name}.jsonl"
        if not os.path.exists(path):
            print(f"{name:11} missing, skipped")
            continue
        rows = [json.loads(line) for line in open(path, encoding="utf-8")]
        kept = []
        for r in rows:
            digest = hashlib.sha1((r["title"] + r["text"][:2000]).encode()).hexdigest()
            if digest in seen or not keep(r):
                continue
            seen.add(digest)
            if name == "judol" or (name == "phish_id" and len(GAMBLING.findall(r["title"] + " " + r["text"][:3000])) >= 3):
                r["scamType"] = "Online Gambling Scam"
            kept.append(r)
        random.shuffle(kept)
        kept = kept[:cap]
        out += kept
        print(f"{name:11} read {len(rows):5}  kept {len(kept):5}  " + " ".join(f"{s} {sum(r['split'] == s for r in kept)}" for s in ("train", "dev", "test")))
    with open(f"{out_dir}/{batch}.jsonl", "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(batch, "pages", len(out), "scam", sum(r["label"] for r in out))


if __name__ == "__main__":
    main(*sys.argv[1:3])
