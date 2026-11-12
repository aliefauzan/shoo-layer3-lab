"""Collect legit Indonesian pages (homepage + up to 2 same-site pages) as benign examples.

Usage: python -I src/fetch_indo_benign.py data/indo_benign
Static GET only: no JavaScript, no cookies, 500 KB cap, public well-known sites. Writes <n>.html.gz with the URL on line 1.
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
SITES = """
kompas.com detik.com tribunnews.com liputan6.com tempo.co cnnindonesia.com cnbcindonesia.com antaranews.com republika.co.id
kumparan.com suara.com okezone.com sindonews.com merdeka.com viva.co.id jawapos.com bisnis.com kontan.co.id tirto.id
katadata.co.id idntimes.com grid.id pikiran-rakyat.com medcom.id beritasatu.com inews.id jpnn.com bola.com hukumonline.com
indonesia.go.id kemdikbud.go.id kemkes.go.id kemenkeu.go.id pajak.go.id bps.go.id bi.go.id ojk.go.id bmkg.go.id kominfo.go.id
kemnaker.go.id bkn.go.id polri.go.id kpu.go.id mahkamahagung.go.id jakarta.go.id jabarprov.go.id jatengprov.go.id
surabaya.go.id bandung.go.id bpjs-kesehatan.go.id bpjsketenagakerjaan.go.id lpse.lkpp.go.id pom.go.id bnpb.go.id
bca.co.id bri.co.id bankmandiri.co.id bni.co.id btn.co.id cimbniaga.co.id danamon.co.id permatabank.com ocbc.id
bankbsi.co.id bankjago.com dana.id ovo.id gopay.co.id linkaja.id pegadaian.co.id bankmega.com maybank.co.id panin.co.id
tokopedia.com shopee.co.id bukalapak.com blibli.com lazada.co.id zalora.co.id sociolla.com bhinneka.com jd.id ruparupa.com
traveloka.com tiket.com pegipegi.com kai.id garuda-indonesia.com lionair.co.id citilink.co.id agoda.com/id-id
telkomsel.com indosatooredoo.com xl.co.id smartfren.com telkom.co.id indihome.co.id biznetnetworks.com myrepublic.co.id
ui.ac.id itb.ac.id ugm.ac.id its.ac.id ipb.ac.id unair.ac.id undip.ac.id ub.ac.id unpad.ac.id telkomuniversity.ac.id
binus.ac.id uny.ac.id uns.ac.id unhas.ac.id usu.ac.id ut.ac.id uin-suka.ac.id umy.ac.id upi.edu gunadarma.ac.id
gojek.com grab.com/id halodoc.com alodokter.com ruangguru.com zenius.net kitabisa.com jobstreet.co.id glints.com/id
kalibrr.id rumah123.com olx.co.id mobil123.com otomotifnet.gridoto.com cermati.com bibit.id ajaib.co.id stockbit.com
pln.co.id pertamina.com posindonesia.co.id jne.co.id jet.co.id sicepat.com anteraja.id tiki.id astra.co.id unilever.co.id
indofood.com kalbe.co.id kimiafarma.co.id k24klik.com klikdokter.com hellosehat.com sehatq.com orami.co.id fimela.com
kaskus.co.id brainly.co.id wikipedia.org/wiki/Indonesia id.wikipedia.org qureta.com mojok.co vice.com/id nu.or.id
muhammadiyah.or.id pmi.or.id dompetdhuafa.org baznas.go.id cgv.id 21cineplex.com vidio.com rcti.tv sctv.co.id
""".split()

link_re = re.compile(rb'<a\b[^>]*\shref\s*=\s*["\']?([^"\'\s>#]+)', re.I)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html", "Accept-Language": "id-ID,id;q=0.9"})
    with urllib.request.urlopen(req, timeout=12) as res:
        if "html" not in (res.headers.get("content-type") or ""):
            return None, None
        return res.geturl(), res.read(CAP)


def site_pages(site):
    out = []
    try:
        final, body = get("https://" + site)
        if not body:
            return out
        out.append((final, body))
        host = urllib.parse.urlparse(final).hostname
        seen, picks = {final.rstrip("/")}, []
        for m in link_re.finditer(body):
            u = urllib.parse.urljoin(final, m.group(1).decode("utf-8", "ignore"))
            p = urllib.parse.urlparse(u)
            if p.scheme in ("http", "https") and p.hostname == host and len(p.path) > 8 and u.rstrip("/") not in seen:
                seen.add(u.rstrip("/"))
                picks.append(u)
        for u in picks[5:40:17][:2]:  # two links from the middle of the page: articles or sections, not the top menu
            try:
                f2, b2 = get(u)
                if b2:
                    out.append((f2, b2))
            except Exception:
                pass
    except Exception:
        pass
    return out


def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    with ThreadPoolExecutor(16) as pool:
        for pages in pool.map(site_pages, SITES):
            for url, body in pages:
                with gzip.open(os.path.join(out_dir, f"{n:04d}.html.gz"), "wb") as f:
                    f.write(url.encode() + b"\n" + body)
                n += 1
    print("sites", len(SITES), "pages saved", n)


if __name__ == "__main__":
    main(sys.argv[1])
