"""Indonesian-language pages from nyuuzyou/phishing-snapshots (CC0), all 28 files -> <out_dir>/<n>.html.gz (URL on line 1).

Usage: python src/sample_phish_id.py data/phish_id
A page counts as Indonesian when its language tag is Indonesian or its text has several common Indonesian words.
"""
import gzip
import os
import sys
from collections import Counter

import duckdb

WORDS = r"\b(yang|untuk|dengan|silahkan|silakan|anda|masukkan|nomor|daftar|masuk|rekening|hadiah|klaim|nasabah|verifikasi|akun|pulsa|gratis|pinjaman|selamat)\b"


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    c = duckdb.connect()
    c.execute("INSTALL httpfs; LOAD httpfs;")
    per_domain, n = Counter(), 0
    for i in range(28):
        url = f"hf://datasets/nyuuzyou/phishing-snapshots/data/train-{i:05d}-of-00028.parquet"
        try:
            rows = c.execute(
                f"""SELECT domain, final_url, url, html FROM read_parquet('{url}')
                WHERE http_status = 200 AND text_length >= 120 AND has_html
                  AND (lower(language) LIKE 'id%' OR lower(language) = 'in'
                       OR len(regexp_extract_all(lower(extracted_text[:3000]), '{WORDS}')) >= 6)"""
            ).fetchall()
        except Exception as e:
            print("file", i, "ERR", str(e)[:120], flush=True)
            continue
        for domain, final_url, first_url, html in rows:
            if per_domain[domain] >= 3:
                continue
            per_domain[domain] += 1
            with gzip.open(os.path.join(out_dir, f"{n:05d}.html.gz"), "wt", encoding="utf-8") as f:
                f.write((final_url or first_url) + "\n" + html[:500_000])
            n += 1
        print("file", i, "matched", len(rows), "kept so far", n, flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
