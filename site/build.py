"""Builds compare.html from template.html + the sample data. Usage: python3 -I site/build.py site"""
import json
import sys

d = sys.argv[1]
rows = json.load(open(f"{d}/sample.json", encoding="utf-8"))
signals = json.load(open(f"{d}/signals.json", encoding="utf-8"))
evidence = json.load(open(f"{d}/evidence.json", encoding="utf-8"))
for r in rows:
    s = signals[r["id"]]
    r["read"] = s["read"]
    r["im"]["sig"] = [{"layer": x["layer"], "pts": x.get("weight", 0), "label": x["label"]["id"], "ev": x.get("evidence") or ""} for x in s["signals"]]
    r["ev"] = evidence[r["id"]]
# Scraped page text is hostile and sometimes badly encoded: no closing tags, no replacement characters.
data = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/").replace("�", "")
open(f"{d}/compare.html", "w", encoding="utf-8").write(open(f"{d}/template.html", encoding="utf-8").read().replace("__DATA__", data))
print("rows", len(rows))
