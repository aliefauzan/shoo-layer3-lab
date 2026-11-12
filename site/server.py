"""Local compare site: paste URLs, both architectures answer, side by side.

Run from the project root:  uv run python site/server.py     then open http://127.0.0.1:8799
  Shipped pipeline: site/reader.mjs (headless Chromium + Shoo's own code: layers 1-2, layer 3 rules, domain age, layer 4).
  Rebuilt pipeline: here (rebuilt layer 3 from out/layer3_*.joblib, then the scam-judge prompt for the gate and warn tiers).
Listens on 127.0.0.1 only. The LLM key is read from ai/.dev.vars into memory and never printed or sent to the page.
"""
import json
import re
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from embed import chunks, encode, load_model  # noqa: E402
from train_eval import Base, is_indonesian, side_matrix  # noqa: E402,F401  (Base is needed to unpickle the bundle)

SHOO_AI = Path.home() / "epel/Final-CH7/ai"
SITE = ROOT / "site"
SHOTS = SITE / "shots"
PORT = 8799
FACTS = ["official", "brandOffsite", "brandTextOffsite", "brandInHost", "cheapTld", "sharedHost", "ipHost", "punycode", "urlKeywords", "passwordField", "formActionOffsite"]

print("loading models…", flush=True)
# Safe to unpickle: both files are written by src/train_eval.py on this machine, never downloaded.
BASE = joblib.load(ROOT / "out/layer3_base.joblib")["base"]
META = joblib.load(ROOT / "out/layer3_meta.joblib")
MODEL = load_model()
TERMS = BASE.word_vec.get_feature_names_out()
WORD_COEF = BASE.words.coef_[0][: len(BASE.word_vec.vocabulary_)]
# One source for the judge prompt: the script that was measured on the held-out pages.
PROMPT = re.search(r"const SYSTEM_PROMPT = `(.*?)`;", (ROOT / "src/layer4_v5.mjs").read_text(encoding="utf-8"), re.S).group(1)
ENV = dict(line.split("=", 1) for line in (SHOO_AI / ".dev.vars").read_text().splitlines() if re.match(r"^[A-Z_]+=", line))
ENV = {k: v.strip().strip("\"'") for k, v in ENV.items()}
LLM_READY = all(ENV.get(k) for k in ("LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL")) and ENV["LLM_BASE_URL"].startswith("https://")
MODEL_LOCK = threading.Lock()
RUN_LOCK = threading.Lock()
JOBS = {}


def judge(row, extra):
    """The scam-judge prompt (src/layer4_v5.mjs), same input shape, same model settings as Shoo's provider."""
    if not LLM_READY:
        return {"failed": "layer 4 belum diatur"}
    text = row["text"]
    mid = max(0, len(text) // 2 - 350)
    excerpts = [text] if len(text) <= 3200 else [text[:1800], text[mid : mid + 700], text[-700:]]
    data = {
        "host": row["host"],
        "path": urlsplit(row["url"]).path[:80],
        "host_is_known_official_brand_site": bool(row["feats"].get("official")),
        "title": row["title"][:200],
        "lang": extra.get("lang"),
        "text_excerpts": excerpts,
        "form_fields": extra.get("fields", []),
        "image_alts": extra.get("imageAlts", []),
        "earlier_rule_signals": row["baseline"]["signals"],
    }
    body = {
        "model": ENV["LLM_MODEL"],
        "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": f"DATA_START\n{json.dumps(data, ensure_ascii=False)}\nDATA_END"}],
        "temperature": 0,
        "max_tokens": 220,
        "response_format": {"type": "json_object"},
        "chat_template_kwargs": {"thinking": False},
    }
    request = urllib.request.Request(
        ENV["LLM_BASE_URL"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "authorization": "Bearer " + ENV["LLM_API_KEY"]},
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=20) as res:
            content = json.load(res)["choices"][0]["message"]["content"]
        answer = json.loads(re.sub(r"^```(?:json)?\s*|```$", "", content.strip()))
        if answer.get("verdict") not in ("scam", "legit", "unsure"):
            raise ValueError
        confidence = answer.get("confidence")
        return {
            "j": answer["verdict"],
            "cat": str(answer.get("category") or "")[:30],
            "conf": float(confidence) if isinstance(confidence, (int, float)) and 0 <= confidence <= 1 else None,
            "ev": str(answer.get("evidence") or "")[:200],
            "ms": round(1000 * (time.time() - started)),
        }
    except Exception:  # never forward what the provider said about an error
        return {"failed": "juri tidak menjawab"}


def rebuilt(row, extra, age_days=None):
    with MODEL_LOCK:
        parts = chunks(row)
        emb = encode(MODEL, parts)
        scores = BASE.score([row], emb, np.zeros(len(parts), dtype=np.int32), emb.mean(axis=0, keepdims=True))
        side = side_matrix([row])
        with_address = float(META["metas"]["with_address"].predict_proba(np.hstack([scores, side]))[0, 1])
        no_address = float(META["metas"]["no_address"].predict_proba(np.hstack([scores[:, :4], side[:, META["no_address_columns"]]]))[0, 1])
        chunk_p, gamble_p = BASE.chunk.predict_proba(emb)[:, 1], BASE.gamble.predict_proba(emb)[:, 1]
    final = (with_address + no_address) / 2
    indo = bool(is_indonesian(row))
    limits = META["thresholds"]["indonesian" if indo else "other"]
    official = bool(row["feats"].get("official"))
    tier = "safe" if official else next((t for t in ("block", "warn", "gate") if final >= limits[t]), "safe")
    best = int(np.argmax(np.maximum(chunk_p, gamble_p)))
    pull = BASE.word_vec.transform([row["title"] + " \n " + row["text"][:6000]]).multiply(WORD_COEF).tocoo()
    top = sorted(zip(pull.data, pull.col), reverse=True)[:6]
    evidence = {
        "final": round(final, 4),
        "limits": {k: round(float(v), 4) for k, v in limits.items()},
        "indo": indo,
        "official": official,
        "base": {n: round(float(v), 3) for n, v in zip(Base.NAMES, scores[0])},
        "chunk": {"text": parts[best].split("\n", 1)[-1][:260], "kind": "gambling" if gamble_p[best] > chunk_p[best] else "scam", "score": round(float(max(chunk_p[best], gamble_p[best])), 3)},
        "words": [TERMS[c] for v, c in top if v > 0],
        "facts": [k for k in FACTS if row["feats"].get(k)],
    }
    us = {"tier": tier}
    # A page layer 3 calls safe still gets a second look when the domain is under 180 days old and the page or the
    # address carries a brand name that does not own the domain (live rule on top of the model; needs the domain age).
    facts = row["feats"]
    if tier == "safe" and not official and age_days is not None and age_days < 180 and (facts.get("brandInHost") or facts.get("brandOffsite") or facts.get("brandTextOffsite")):
        us = {"tier": "gate", "raised": True}
        tier = "gate"
    if tier in ("gate", "warn"):
        us.update(judge(row, extra))
    return us, evidence


def run_job(job, urls):
    with RUN_LOCK:
        shots = SHOTS / job["id"]
        proc = subprocess.Popen(["node", str(SITE / "reader.mjs"), str(SHOO_AI), str(shots)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=str(ROOT))
        proc.stdin.write(json.dumps(urls))
        proc.stdin.close()
        for line in proc.stdout:
            try:
                page = json.loads(line)
            except ValueError:
                continue
            out = {"n": page["n"], "url": page["url"], "finalUrl": page.get("finalUrl"), "status": page.get("status"), "error": page.get("error")}
            if page.get("shot"):
                out["shot"] = f"/shots/{job['id']}/{page['shot']}"
            if page.get("row"):
                row = {**page["row"], "url": page["finalUrl"]}
                try:
                    registered = page["imelda"].get("registeredAt")
                    age_days = (time.time() - time.mktime(time.strptime(str(registered)[:10], "%Y-%m-%d"))) / 86400 if registered else None
                    us, evidence = rebuilt(row, page.get("llmInput", {}), age_days)
                    out.update(host=row["host"], title=row["title"][:120], text=re.sub(r"\s+", " ", row["text"][:260]), read=page["read"], im=page["imelda"], us=us, ev=evidence)
                except Exception:
                    out["error"] = "Layer 3 baru gagal menilai halaman ini."
            job["results"].append(out)
        proc.wait()
        job["done"] = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(data)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def local(self):
        """Only this machine's browser: refuses another site's page that points a name at 127.0.0.1."""
        return (self.headers.get("host") or "").split(":")[0] in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self.local():
            return self.send(403, {"error": "local only"})
        path = urlsplit(self.path).path
        if path == "/":
            return self.send(200, (SITE / "index.html").read_bytes(), "text/html; charset=utf-8")
        job = re.fullmatch(r"/api/job/([0-9a-f]{32})", path)
        if job and job.group(1) in JOBS:
            found = JOBS[job.group(1)]
            start = re.fullmatch(r"from=(\d+)", urlsplit(self.path).query)
            start = int(start.group(1)) if start else 0  # the page asks only for results it does not have yet
            return self.send(200, {"total": found["total"], "done": found["done"], "count": len(found["results"]), "results": found["results"][start:]})
        shot = re.fullmatch(r"/shots/([0-9a-f]{32})/(\d+)\.jpg", path)
        if shot and (SHOTS / shot.group(1) / f"{shot.group(2)}.jpg").is_file():
            return self.send(200, (SHOTS / shot.group(1) / f"{shot.group(2)}.jpg").read_bytes(), "image/jpeg")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.local() or self.headers.get("content-type", "").split(";")[0] != "application/json":
            return self.send(403, {"error": "local only"})
        if urlsplit(self.path).path != "/api/run":
            return self.send(404, {"error": "not found"})
        try:
            urls = json.loads(self.rfile.read(min(int(self.headers.get("content-length", 0)), 20_000_000)))["urls"]
            urls = [u for u in urls if isinstance(u, str) and re.match(r"^https?://[^\s/]+", u) and len(u) <= 2000]
        except Exception:
            return self.send(400, {"error": "Kirim daftar URL http atau https."})
        if not urls:
            return self.send(400, {"error": "Isi minimal satu URL http atau https."})
        job = {"id": uuid.uuid4().hex, "total": len(urls), "done": False, "results": []}
        JOBS[job["id"]] = job
        threading.Thread(target=run_job, args=(job, urls), daemon=True).start()
        self.send(200, {"id": job["id"], "total": job["total"]})


if __name__ == "__main__":
    print(f"ready: http://127.0.0.1:{PORT}  (layer 4 {'on' if LLM_READY else 'off'})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
