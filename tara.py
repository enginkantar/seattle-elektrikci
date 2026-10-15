#!/usr/bin/env python3
"""Seattle elektrik müteahhidi taraması (Socrata SODA API, c4tj-daue).

Kullanım:
    python3 tara.py --zip 98115 [--baslangic 2024-09-01] [--cikti ~/mutfak]

Adımlar:
  1. Şema doğrulama (1 satır çek, beklenen kolonları teyit et; eşmezse
     columns.json'dan güncel şemayı al ve farkı raporla).
  2. ZIP'te son 24 ayda izin çekmiş müteahhitleri iş sayısına göre sırala.
  3. Aynı ZIP'te description'da kitchen/remodel geçen izinleri listele,
     Adım 2 ile kesiştir (A takımı).
  4. elektrikci-adaylar.csv + elektrikci-ozet.md yaz.

KURAL: Veri uydurulmaz. API bir alanı boş döndürüyorsa "veri yok" yazılır.
Yalnız Python standart kütüphanesi kullanılır (harici bağımlılık yok).
"""
import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date

RESOURCE = "https://data.seattle.gov/resource/c4tj-daue.json"
SCHEMA_URL = "https://cos-data.seattle.gov/api/views/c4tj-daue/columns.json"

BEKLENEN_KOLONLAR = [
    "permitnum", "permitclass", "permittypemapped", "description",
    "estprojectcost", "applieddate", "issueddate", "expiresdate",
    "completeddate", "statuscurrent", "originaladdress1", "originalzip",
    "contractorcompanyname", "link", "latitude", "longitude",
]

VERI_YOK = "veri yok"


# Kesin eşleşme: Socrata'nın alt kümesi kolonlarla karşılaştırma yapmak için.
MUTFAK_ANAHTARLAR = ["kitchen", "remodel"]  # remodel-residential de 'remodel' içerir


def soda_get(base, params, timeout=90):
    url = base + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                "User-Agent": "bitmedenal-tara/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        sys.exit(f"HATA: {url}\n  HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        sys.exit(f"HATA: {url}\n  bağlantı: {e.reason}")


def bos(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def deger(row, key):
    v = row.get(key)
    if isinstance(v, dict):          # Socrata URL tipi: {"url": "..."}
        v = v.get("url")
    return VERI_YOK if bos(v) else str(v)


def tarih(v):
    """Socrata floating timestamp -> YYYY-MM-DD; boşsa 'veri yok'."""
    return VERI_YOK if bos(v) else str(v)[:10]


def adim1_sema(base, schema_url):
    print("ADIM 1 — şema doğrulama", file=sys.stderr)
    rows = soda_get(base, {"$limit": 1})
    if not rows:
        sys.exit("HATA: API 0 satır döndürdü; şema doğrulanamadı.")
    gelen = set(rows[0].keys())
    # Not: Socrata null alanları JSON'a hiç koymaz; tek satırdan eksik görünen
    # kolon 'yok' demek değildir. Eksik görünenleri columns.json ile teyit et.
    eksik = [k for k in BEKLENEN_KOLONLAR if k not in gelen]
    if eksik:
        print(f"  tek satırda görünmeyen kolonlar: {eksik} — columns.json kontrol ediliyor",
              file=sys.stderr)
        try:
            cols = soda_get(schema_url, {})
            adlar = {c.get("fieldName") for c in cols}
            gercek_eksik = [k for k in eksik if k not in adlar]
            if gercek_eksik:
                sys.exit(f"HATA: şema eşmiyor, veri setinde olmayan kolonlar: {gercek_eksik}\n"
                         f"  güncel kolonlar: {sorted(a for a in adlar if a)}")
            print("  columns.json: beklenen tüm kolonlar mevcut ✓", file=sys.stderr)
        except SystemExit:
            raise
    else:
        print("  16/16 kolon tek satırda görüldü ✓", file=sys.stderr)


def adim2_aktif(base, zip_kodu, baslangic, limit):
    print(f"ADIM 2 — ZIP {zip_kodu}, {baslangic} sonrası, müteahhit bazında sayım",
          file=sys.stderr)
    where = (f"originalzip='{zip_kodu}' AND issueddate > '{baslangic}T00:00:00.000' "
             f"AND contractorcompanyname IS NOT NULL")
    rows = soda_get(base, {
        "$select": "contractorcompanyname, count(*) as n",
        "$where": where,
        "$group": "contractorcompanyname",
        "$order": "n DESC",
        "$limit": str(limit),
    })
    return [(r["contractorcompanyname"], int(r["n"])) for r in rows]


def adim3_mutfak(base, zip_kodu, baslangic):
    print("ADIM 3 — mutfak/remodel izinleri", file=sys.stderr)
    # lower() ile büyük/küçük harf duyarsız; remodel-residential 'remodel' içerir.
    desc = " OR ".join(f"lower(description) like '%{k}%'" for k in MUTFAK_ANAHTARLAR)
    where = (f"originalzip='{zip_kodu}' AND issueddate > '{baslangic}T00:00:00.000' "
             f"AND contractorcompanyname IS NOT NULL AND ({desc})")
    out, offset, page = [], 0, 1000
    while True:
        rows = soda_get(base, {
            "$select": ",".join(BEKLENEN_KOLONLAR),
            "$where": where,
            "$order": "issueddate DESC",
            "$limit": str(page),
            "$offset": str(offset),
        })
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
    return out


def adim3b_ornekler(base, zip_kodu, baslangic, sirketler):
    """Her müteahhit için en yeni izni (örnek permitnum/adres/link) çek."""
    print("ADIM 3b — her firma için en yeni izin", file=sys.stderr)
    ornek = {}
    for ad in sirketler:
        ad_esc = ad.replace("'", "''")
        rows = soda_get(base, {
            "$select": "permitnum,originaladdress1,link,issueddate,description",
            "$where": (f"originalzip='{zip_kodu}' AND issueddate > '{baslangic}T00:00:00.000' "
                       f"AND contractorcompanyname='{ad_esc}'"),
            "$order": "issueddate DESC",
            "$limit": "1",
        })
        ornek[ad] = rows[0] if rows else {}
    return ornek


def yaz(cikti, zip_kodu, baslangic, aktif, mutfak_rows, ornekler, adres):
    os.makedirs(cikti, exist_ok=True)
    mutfak_by = defaultdict(list)
    for r in mutfak_rows:
        mutfak_by[r["contractorcompanyname"]].append(r)

    kayitlar = []
    for ad, n in aktif:
        m = mutfak_by.get(ad, [])
        # Örnek izin: mutfak izni varsa onu tercih et (denetim geçmişi için).
        o = m[0] if m else ornekler.get(ad, {})
        kayitlar.append({
            "sirket_adi": ad,
            "toplam_izin_sayisi": n,
            "mutfak_izin_sayisi": len(m),
            "ornek_permitnum": deger(o, "permitnum"),
            "ornek_adres": deger(o, "originaladdress1"),
            "ornek_link": deger(o, "link"),
            "son_izin_tarihi": tarih(ornekler.get(ad, {}).get("issueddate")),
        })
    kayitlar.sort(key=lambda k: (-k["mutfak_izin_sayisi"], -k["toplam_izin_sayisi"]))

    csv_yol = os.path.join(cikti, "elektrikci-adaylar.csv")
    with open(csv_yol, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["sirket_adi", "toplam_izin_sayisi",
                                          "mutfak_izin_sayisi", "ornek_permitnum",
                                          "ornek_adres", "ornek_link", "son_izin_tarihi"])
        w.writeheader()
        w.writerows(kayitlar)

    a_takimi = [k for k in kayitlar if k["mutfak_izin_sayisi"] > 0]
    tek_seferlik = [k for k in kayitlar if k["toplam_izin_sayisi"] == 1]
    # A takımı boşsa şeffaf biçimde toplam izne göre ilk 10 verilir.
    ilk10 = (a_takimi or kayitlar)[:10]

    L = []
    L.append(f"# Seattle elektrikçi adayları — ZIP {zip_kodu}")
    L.append("")
    L.append(f"- Referans adres: {adres}")
    L.append(f"- Kaynak: Seattle Open Data `c4tj-daue` (Building Permits), SODA API")
    L.append(f"- Kapsam: `issueddate > {baslangic}` (son 24 ay), `originalzip = {zip_kodu}`")
    L.append(f"- Üretim tarihi: {date.today().isoformat()}")
    L.append(f"- Aktif müteahhit (Adım 2): {len(aktif)} firma")
    L.append(f"- Mutfak/remodel izni (Adım 3): {len(mutfak_rows)} izin, "
             f"{len(mutfak_by)} firma; kesişim (A takımı): {len(a_takimi)} firma")
    L.append("")
    L.append("Not: 'veri yok' = API o alanı boş döndürdü; tahmin edilmedi.")
    L.append("")
    L.append("## İlk 10 firma (A takımı: hem aktif hem mutfak işi)")
    L.append("")
    if not a_takimi:
        L.append("Bu ZIP'te mutfak/remodel izniyle kesişen firma bulunamadı (veri yok). "
                 "Aşağıdaki liste yalnız toplam izin sayısına göredir.")
        L.append("")
    for i, k in enumerate(ilk10, 1):
        L.append(f"### {i}. {k['sirket_adi']}")
        L.append(f"- Neden: son 24 ayda **{k['toplam_izin_sayisi']}** izin (iş hacmi), "
                 f"bunların **{k['mutfak_izin_sayisi']}** tanesi kitchen/remodel (mutfak tecrübesi).")
        L.append(f"- Örnek izin: `{k['ornek_permitnum']}` — {k['ornek_adres']}")
        L.append(f"- Portal: {k['ornek_link']}")
        L.append(f"- Son izin tarihi: {k['son_izin_tarihi']}")
        L.append("")

    L.append("## Aktif ama mutfak izni görünmeyen firmalar (B takımı)")
    L.append("")
    b = [k for k in kayitlar if k["mutfak_izin_sayisi"] == 0 and k["toplam_izin_sayisi"] > 1]
    if not b:
        L.append("(yok)")
    for k in b:
        L.append(f"- {k['sirket_adi']} — {k['toplam_izin_sayisi']} izin, "
                 f"örnek `{k['ornek_permitnum']}`, son {k['son_izin_tarihi']}")
    L.append("")

    L.append("## Elemek için (tek seferlik / şüpheli)")
    L.append("")
    L.append("Son 24 ayda bu ZIP'te yalnız 1 izin: düzenli çalışmıyor olabilir ya da "
             "tek iş için gelmiştir. Portal'dan denetim geçmişine bakmadan seçme.")
    L.append("")
    if not tek_seferlik:
        L.append("(yok)")
    for k in tek_seferlik:
        etiket = " (mutfak izni)" if k["mutfak_izin_sayisi"] else ""
        L.append(f"- {k['sirket_adi']}{etiket} — örnek `{k['ornek_permitnum']}`, "
                 f"{k['ornek_adres']}, son {k['son_izin_tarihi']}")
    L.append("")
    L.append("## Yöntem")
    L.append("")
    L.append("1. Şema doğrulandı (16 kolon).")
    L.append(f"2. `$group=contractorcompanyname`, `$order=n DESC`, `$limit=50`.")
    L.append("3. `lower(description) like '%kitchen%' OR '%remodel%'` (remodel-residential dahil).")
    L.append("4. Sıralama: önce mutfak izni sayısı, sonra toplam izin sayısı.")
    L.append("")
    md_yol = os.path.join(cikti, "elektrikci-ozet.md")
    with open(md_yol, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return csv_yol, md_yol, kayitlar


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", required=True, help="Seattle ZIP kodu, örn. 98115")
    ap.add_argument("--adres", default="(verilmedi)",
                    help="özet başlığına yazılacak referans adres (sorguyu etkilemez)")
    ap.add_argument("--baslangic", default="2024-09-01",
                    help="issueddate alt sınırı (YYYY-MM-DD), varsayılan 2024-09-01")
    ap.add_argument("--cikti", default=os.path.expanduser("~/mutfak"),
                    help="çıktı dizini, varsayılan ~/mutfak")
    ap.add_argument("--limit", type=int, default=50, help="Adım 2 firma sayısı (50)")
    ap.add_argument("--base", default=RESOURCE, help=argparse.SUPPRESS)
    ap.add_argument("--schema-url", default=SCHEMA_URL, help=argparse.SUPPRESS)
    a = ap.parse_args()

    if not (a.zip.isdigit() and len(a.zip) == 5):
        sys.exit("HATA: ZIP 5 haneli olmalı, örn. --zip 98115")

    adim1_sema(a.base, a.schema_url)
    aktif = adim2_aktif(a.base, a.zip, a.baslangic, a.limit)
    if not aktif:
        print(f"UYARI: ZIP {a.zip} için {a.baslangic} sonrası müteahhitli izin yok.",
              file=sys.stderr)
    mutfak = adim3_mutfak(a.base, a.zip, a.baslangic)
    ornekler = adim3b_ornekler(a.base, a.zip, a.baslangic, [ad for ad, _ in aktif])
    csv_yol, md_yol, kayitlar = yaz(a.cikti, a.zip, a.baslangic, aktif, mutfak, ornekler,
                                    a.adres)

    a_tak = sum(1 for k in kayitlar if k["mutfak_izin_sayisi"] > 0)
    print(f"\nTAMAM: {len(kayitlar)} firma, {a_tak} A takımı, {len(mutfak)} mutfak izni",
          file=sys.stderr)
    print(f"  {csv_yol}\n  {md_yol}", file=sys.stderr)


if __name__ == "__main__":
    main()
