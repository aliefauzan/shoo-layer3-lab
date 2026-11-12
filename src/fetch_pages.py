"""Fetch pages for a list of sites or URLs. Static GET only: no JavaScript, no cookies, 500 KB cap.

  python3 -I src/fetch_pages.py sites <out_dir> <file with one domain per line>   homepage + a login/checkout-like page + one other page
  python3 -I src/fetch_pages.py urls  <out_dir> <file with one URL per line>      exactly those URLs (used for reported scam URLs)

Writes <n>.html.gz with the final URL on line 1. Scam pages are hostile: the HTML is stored and parsed as text only.
"""
import gzip
import os
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
CAP = 500_000
LINK = re.compile(rb'<a\b[^>]*\shref\s*=\s*["\']?([^"\'\s>#]+)', re.I)
LOGINISH = re.compile(r"log-?in|sign-?in|sign-?up|masuk|daftar|register|account|akun|cart|keranjang|checkout|pembayaran|payment|auth|sso|member", re.I)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Language": "id-ID,id;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=10) as res:
        if "html" not in (res.headers.get("content-type") or ""):
            return None, None
        return res.geturl(), res.read(CAP)


def site_pages(site):
    out = []
    try:
        final, body = get("https://" + site)
    except Exception:
        try:
            final, body = get("http://" + site)
        except Exception:
            return out
    if not body:
        return out
    out.append((final, body))
    host = urllib.parse.urlparse(final).hostname or ""
    base = ".".join(host.split(".")[-2:])
    seen, loginish, other = {final.rstrip("/")}, [], []
    for m in LINK.finditer(body):
        u = urllib.parse.urljoin(final, m.group(1).decode("utf-8", "ignore"))
        p = urllib.parse.urlparse(u)
        if p.scheme not in ("http", "https") or not (p.hostname or "").endswith(base) or u.rstrip("/") in seen:
            continue
        seen.add(u.rstrip("/"))
        (loginish if LOGINISH.search(p.path + " " + (p.hostname or "")) else other).append(u)
    picks = loginish[:1] + [u for u in other if len(urllib.parse.urlparse(u).path) > 8][5:6]
    for u in picks:
        try:
            f2, b2 = get(u)
            if b2:
                out.append((f2, b2))
        except Exception:
            pass
    return out


def one_url(url):
    try:
        final, body = get(url)
        return [(final, body)] if body else []
    except Exception:
        return []


def main(mode, out_dir, list_path):
    os.makedirs(out_dir, exist_ok=True)
    items = [line.strip() for line in open(list_path, encoding="utf-8") if line.strip()]
    worker = site_pages if mode == "sites" else one_url
    n = 0
    with ThreadPoolExecutor(32) as pool:
        for pages in pool.map(worker, items):
            for url, body in pages:
                with gzip.open(os.path.join(out_dir, f"{n:05d}.html.gz"), "wb") as f:
                    f.write(url.encode() + b"\n" + body)
                n += 1
    print(mode, len(items), "items, pages saved", n)


if __name__ == "__main__":
    main(*sys.argv[1:4])
