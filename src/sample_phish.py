"""Sample real phishing pages from nyuuzyou/phishing-snapshots (CC0) into <out_dir>/<n>.html.gz (URL on line 1).

Usage: python src/sample_phish.py data/phish 6
Reads the first N parquet files remotely. Keeps pages that still served content (status 200, some text) and drops
takedown notices and link-shortener warnings, which are not phishing content. At most 2 pages per domain and 4 per
title, so one phishing kit cannot dominate.
"""
import gzip
import os
import re
import sys
from collections import Counter

import duckdb

NOT_PHISHING = re.compile(
    r"blocked by bitly|suspected phishing|deceptive site|account (has been )?suspended|page not found|404 not found|"
    r"domain (is )?for sale|this site can.t be reached|access denied|attention required|just a moment|site not found|"
    r"web server is down|coming soon|under construction|index of /|default web page|apache2|welcome to nginx|parked",
    re.I,
)


def main(out_dir, files):
    os.makedirs(out_dir, exist_ok=True)
    c = duckdb.connect()
    c.execute("INSTALL httpfs; LOAD httpfs;")
    per_domain, per_title, n = Counter(), Counter(), 0
    for i in range(int(files)):
        url = f"hf://datasets/nyuuzyou/phishing-snapshots/data/train-{i:05d}-of-00028.parquet"
        cur = c.execute(f"SELECT domain, final_url, url, title, extracted_text, html FROM read_parquet('{url}') WHERE http_status = 200 AND text_length >= 150 AND has_html")
        while True:
            rows = cur.fetchmany(200)
            if not rows:
                break
            for domain, final_url, first_url, title, text, html in rows:
                title = (title or "").strip().lower()
                if NOT_PHISHING.search((title + " " + (text or ""))[:1500]) or per_domain[domain] >= 2 or per_title[title] >= 4:
                    continue
                per_domain[domain] += 1
                per_title[title] += 1
                with gzip.open(os.path.join(out_dir, f"{n:05d}.html.gz"), "wt", encoding="utf-8") as f:
                    f.write((final_url or first_url) + "\n" + html[:500_000])
                n += 1
        print("file", i, "kept so far", n, flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:3])
