"""Look up the registration date of every site in the dataset (RDAP, public registry data).

Usage: python3 -I src/rdap_ages.py out data/rdap.jsonl
Appends one line per domain: {"domain", "registered" (ISO date or null), "status"}. Safe to stop and rerun: domains
already in the file are skipped. Only the bare domain name is sent, never a page URL.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SECOND_LEVEL = re.compile(r"^(co|go|ac|or|web|sch|my|biz|net|com|org|gov|edu|mil|desa|ponpes)\.[a-z]{2}$")


def site(host):
    p = host.removeprefix("www.").split(".")
    return ".".join(p[-3:] if SECOND_LEVEL.match(".".join(p[-2:])) else p[-2:])


BOOTSTRAP = {}


def load_bootstrap(path="data/lists/rdap_bootstrap.json"):
    """IANA list: which registry answers RDAP for each top-level domain. Asking registries directly avoids one shared limit."""
    for tlds, urls in json.load(open(path))["services"]:
        for tld in tlds:
            BOOTSTRAP[tld.lower()] = urls[0].rstrip("/")


def lookup(domain):
    base = BOOTSTRAP.get(domain.rsplit(".", 1)[-1])
    if not base:
        return {"domain": domain, "registered": None, "status": "no-rdap"}
    for attempt in range(4):
        try:
            req = urllib.request.Request(f"{base}/domain/{domain}", headers={"Accept": "application/rdap+json", "User-Agent": "rdap-age-lookup"})
            with urllib.request.urlopen(req, timeout=12) as res:
                data = json.load(res)
            for event in data.get("events", []):
                if event.get("eventAction") == "registration":
                    return {"domain": domain, "registered": event.get("eventDate"), "status": "ok"}
            return {"domain": domain, "registered": None, "status": "no-date"}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(8 * (attempt + 1))
                continue
            return {"domain": domain, "registered": None, "status": f"http-{e.code}"}
        except Exception:
            time.sleep(2)
    return {"domain": domain, "registered": None, "status": "failed"}


def main(out_dir, path):
    domains = set()
    for name in ("pages", "extra", "corpus"):
        for line in open(f"{out_dir}/{name}.jsonl", encoding="utf-8"):
            host = json.loads(line)["host"]
            if host and not re.fullmatch(r"[0-9.]+", host):
                domains.add(site(host))
    done = set()
    if os.path.exists(path):
        done = {json.loads(line)["domain"] for line in open(path, encoding="utf-8")}
    load_bootstrap()
    # Registries are interleaved (sorted by reversed name would group one TLD together and trip its rate limit).
    todo = sorted(domains - done, key=lambda d: hash(d))
    print("domains", len(domains), "to look up", len(todo), flush=True)
    with open(path, "a", encoding="utf-8") as f, ThreadPoolExecutor(10) as pool:
        for i, result in enumerate(pool.map(lookup, todo)):
            f.write(json.dumps(result) + "\n")
            if i % 500 == 0:
                f.flush()
                print(i, flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:3])
