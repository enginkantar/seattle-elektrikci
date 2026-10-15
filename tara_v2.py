#!/usr/bin/env python3
"""Seattle konut elektrikçisi taraması v2 — yarıçap + konut sınıfı + ölçek filtresi.

Kullanım:
    python3 tara_v2.py --sokak "PINE ST" --zip 98122 --no 1500       # evinizin sokağı/ZIP/kapı no
    python3 tara_v2.py --lat 47.605 --lon -122.323                   # ya da doğrudan koordinat
    python3 tara_v2.py --zipsiz                                      # ya da tüm Seattle, yarıçapsız
  Seçenekler: [--yaricap 3200] [--baslangic 2023-09-01] [--cikti ~/mutfak]
              [--sinif "Residential" ...] [--maks-ort-maliyet 200000]

Adımlar:
  1. permitclassmapped / permitclass gerçek değerlerini sorgula ve yazdır;
     konut değerlerini seç (heuristik: 'residential' içerip 'non-residential'
     içermeyen; --sinif ile elle verilebilir).
  2. Referans noktası VERİDEN: aynı sokak + ZIP'teki izin kayıtlarından kapı
     numarası hedefe en yakın olanın lat/lon'u. Geocoding servisi yok.
  3. within_circle(<nokta kolonu>, lat, lon, yarıçap) + konut sınıfı + tarih +
     müteahhit dolu; nokta kolonu yoksa tüm konut izinleri çekilip yarıçap
     istemci tarafında (haversine) uygulanır. 15 firmanın altında kalırsa
     yarıçap 8000'e çıkar (raporlanır).
  4. Firma bazında avg/min/max estprojectcost; ortalama > eşik → ELENDİ (ticari ölçek).
  5. Ad bazlı sert eleme (fire, security, protection, controls, ...).
  6. Tekilleştirme: büyük harf, noktalama ve Inc/LLC/Corp ekleri sökülür.

Çıktı: elektrikci-adaylar-v2.csv + ozet-v2.md
KURAL: Kolon değerleri uydurulmaz; ADIM 1 atlanmaz. Yalnız standart kütüphane.
"""
import argparse
import csv
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date

RESOURCE = "https://data.seattle.gov/resource/c4tj-daue.json"
SCHEMA_URL = "https://cos-data.seattle.gov/api/views/c4tj-daue/columns.json"
VERI_YOK = "veri yok"

# Sert eleme: low-voltage / yangın / mekanik / güneş / kablolama / ses-görüntü.
# (İlk canlı koşum ADT, Vivint, Puget Sound Solar, Skyline Communications gibi
# firmaların ilk filtreden geçtiğini gösterdi; liste ona göre genişletildi.)
ELEME_KELIMELER = ["fire", "security", "protection", "controls", "systems integration",
                   "integrated", "integration", "av ", "audio", "video", "facility",
                   "facilities", "mechanical", "technologies", "technology", "power systems",
                   "alarm", "communications", "cabling", "wireless", "solar", "natural gas",
                   "refrigeration", "roofing", "cleaning", "fence", "gates", "permitting",
                   "hearth", "exteriors", "generators", "adt ", "vivint", "everon", "kastle",
                   "siemens", "schneider", "best buy", "lumio", "wipliance"]
# Yumuşak etiket: çok branşlı ev hizmetleri (ısıtma/tesisat/inşaat). Elenmez,
# tabloda 'çok-branşlı' notu düşülür; tek daire mutfak devresi için genelde
# saf elektrik müteahhidi tercih edilir.
COK_BRANS_KELIMELER = ["heating", "hvac", "air", "a/c", "plumbing", "construction",
                       "renovation", "home services", "home solutions", "lighting",
                       "energy", "services"]
BAND_ALT, BAND_UST = 3000.0, 25000.0   # bizim işin ölçeği ($3k–$25k)
EK_KELIMELER = {"INC", "LLC", "CORP", "CORPORATION", "CO", "LTD", "LP", "PLLC",
                "INCORPORATED", "COMPANY", "THE"}

SATIR_KOLONLARI = ["permitnum", "permitclass", "permitclassmapped", "description",
                   "estprojectcost", "issueddate", "originaladdress1", "originalzip",
                   "contractorcompanyname", "link", "latitude", "longitude"]


# ---------- yardımcılar ----------
def soda_get(base, params, timeout=120):
    url = base + ("?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote) if params else "")
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                "User-Agent": "bitmedenal-tara-v2/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HATA: {url}\n  HTTP {e.code}: {e.read().decode('utf-8','replace')[:600]}")
    except urllib.error.URLError as e:
        sys.exit(f"HATA: {url}\n  bağlantı: {e.reason}")


def sayfali(base, params, sayfa=5000):
    out, off = [], 0
    while True:
        p = dict(params, **{"$limit": str(sayfa), "$offset": str(off)})
        rows = soda_get(base, p)
        out.extend(rows)
        print(f"    {len(out)} satır", file=sys.stderr)
        if len(rows) < sayfa:
            return out
        off += sayfa


def bos(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def deger(row, key):
    v = row.get(key)
    if isinstance(v, dict):          # Socrata URL tipi: {"url": "..."}
        v = v.get("url")
    return VERI_YOK if bos(v) else str(v)


def sayi(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def normalize(ad):
    s = ad.upper()
    s = re.sub(r"[^A-Z0-9&' ]+", " ", s)          # nokta, virgül vb.
    kelimeler = [k for k in s.split() if k not in EK_KELIMELER]
    return " ".join(kelimeler).strip()


def sql_str(s):
    return "'" + s.replace("'", "''") + "'"


# ---------- adımlar ----------
def adim1_siniflar(base, elle_siniflar):
    print("ADIM 1 — sınıf değerleri (gerçek veriden)", file=sys.stderr)
    mapped = soda_get(base, {"$select": "permitclassmapped, count(*) as n",
                             "$group": "permitclassmapped", "$order": "n DESC"})
    cls = soda_get(base, {"$select": "permitclass, count(*) as n",
                          "$group": "permitclass", "$order": "n DESC", "$limit": "30"})
    print("  permitclassmapped:", file=sys.stderr)
    for r in mapped:
        print(f"    {r.get('permitclassmapped')!r}: {r.get('n')}", file=sys.stderr)
    print("  permitclass (ilk 30):", file=sys.stderr)
    for r in cls:
        print(f"    {r.get('permitclass')!r}: {r.get('n')}", file=sys.stderr)
    if elle_siniflar:
        secilen = elle_siniflar
        kaynak = "--sinif ile elle verildi"
    else:
        secilen = [r["permitclassmapped"] for r in mapped
                   if not bos(r.get("permitclassmapped"))
                   and "residential" in r["permitclassmapped"].lower()
                   and "non-residential" not in r["permitclassmapped"].lower()
                   and "nonresidential" not in r["permitclassmapped"].lower()]
        kaynak = "heuristik: 'residential' içeren, 'non-residential' içermeyen"
    if not secilen:
        sys.exit("HATA: konut sınıfı seçilemedi. Yukarıdaki değerlerden --sinif ile verin.")
    print(f"  seçilen konut sınıfları ({kaynak}): {secilen}", file=sys.stderr)
    return mapped, cls, secilen, kaynak


def nokta_kolonu(schema_url):
    """columns.json'dan point/location tipli kolonu bul (within_circle için)."""
    try:
        cols = soda_get(schema_url, {})
    except SystemExit:
        return None, []
    adlar = [(c.get("fieldName"), c.get("dataTypeName")) for c in cols]
    for ad, tip in adlar:
        if tip in ("point", "location") and ad:
            return ad, adlar
    return None, adlar


def adim2_referans(base, sokak, zipk, kapi_no):
    """Referans noktayı VERİDEN al: aynı sokak + ZIP'teki izin kayıtlarından,
    kapı numarası hedefe en yakın olanın lat/lon'u. Geocoding servisi yok."""
    sokak_u = sokak.upper().strip()
    print(f"ADIM 2 — referans noktası ({kapi_no} {sokak_u}, {zipk}, veriden)", file=sys.stderr)
    rows = soda_get(base, {
        "$select": "originaladdress1, latitude, longitude",
        "$where": f"upper(originaladdress1) like '%{sql_str(sokak_u)[1:-1]}%' AND originalzip='{zipk}'",
        "$limit": "200"})
    aday = []
    for r in rows:
        lat, lon = sayi(r.get("latitude")), sayi(r.get("longitude"))
        if lat is None or lon is None:
            continue
        m = re.match(r"\s*(\d+)", r.get("originaladdress1", ""))
        no = int(m.group(1)) if m else None
        aday.append((abs(no - kapi_no) if no is not None else 10**6, r["originaladdress1"], lat, lon))
    if not aday:
        sys.exit(f"HATA: '{sokak_u}' ({zipk}) üzerinde lat/lon'lu izin kaydı bulunamadı; "
                 "referans noktası veriden alınamadı. --lat/--lon ile verin ya da --zipsiz kullanın.")
    aday.sort()
    fark, adres, lat, lon = aday[0]
    not_ = (f"{kapi_no} {sokak_u} kaydı" if fark == 0 else
            f"{kapi_no} {sokak_u} kaydı yok; en yakın kapı numarası kullanıldı (fark {fark})")
    print(f"  referans: {adres} ({lat}, {lon}) — {not_}", file=sys.stderr)
    return adres, lat, lon, not_, rows


def adim3_cek(base, nokta_kol, lat, lon, yaricap, siniflar, baslangic, zipsiz):
    sinif_sql = ", ".join(sql_str(s) for s in siniflar)
    where = (f"permitclassmapped IN ({sinif_sql}) "
             f"AND issueddate > '{baslangic}T00:00:00.000' "
             f"AND contractorcompanyname IS NOT NULL")
    yontem = "yarıçap filtresi yok (--zipsiz: tüm Seattle)"
    if not zipsiz and nokta_kol:
        where = f"within_circle({nokta_kol}, {lat}, {lon}, {yaricap}) AND " + where
        yontem = f"sunucu tarafı within_circle({nokta_kol}, r={yaricap} m)"
    elif not zipsiz:
        yontem = f"nokta kolonu yok → istemci tarafı haversine (r={yaricap} m)"
    print(f"ADIM 3 — izinler çekiliyor: {yontem}", file=sys.stderr)
    rows = sayfali(base, {"$select": ",".join(SATIR_KOLONLARI), "$where": where,
                          "$order": "issueddate DESC"})
    # Mesafe her durumda istemci tarafında hesaplanır (raporda uzaklık için).
    out = []
    for r in rows:
        rl, rn = sayi(r.get("latitude")), sayi(r.get("longitude"))
        r["_mesafe_m"] = haversine_m(lat, lon, rl, rn) if (rl is not None and rn is not None) else None
        if zipsiz or nokta_kol or (r["_mesafe_m"] is not None and r["_mesafe_m"] <= yaricap):
            out.append(r)
    return out, yontem


def firma_topla(rows):
    """ADIM 6 tekilleştirme + ADIM 4 maliyet istatistikleri (istemci tarafı)."""
    f = {}
    for r in rows:
        ham = r["contractorcompanyname"]
        key = normalize(ham)
        if not key:
            continue
        g = f.setdefault(key, {"varyantlar": defaultdict(int), "izinler": [], "maliyetler": []})
        g["varyantlar"][ham.strip()] += 1
        g["izinler"].append(r)
        c = sayi(r.get("estprojectcost"))
        if c is not None:
            g["maliyetler"].append(c)
    for key, g in f.items():
        g["izinler"].sort(key=lambda r: r.get("issueddate") or "", reverse=True)
        m = g["maliyetler"]
        g["n"] = len(g["izinler"])
        g["ort"] = sum(m) / len(m) if m else None
        g["min"] = min(m) if m else None
        g["max"] = max(m) if m else None
        g["maliyet_kayit"] = len(m)
        ms = sorted(m)
        g["medyan"] = (ms[len(ms)//2] if len(ms) % 2 else (ms[len(ms)//2-1] + ms[len(ms)//2]) / 2) if ms else None
        # Bizim ölçekteki iş sayısı: $3k–$25k bandındaki izinler (ölçek eşleşmesi).
        g["band"] = sum(1 for c in m if BAND_ALT <= c <= BAND_UST)
        g["gorunen_ad"] = max(g["varyantlar"].items(), key=lambda kv: kv[1])[0]
        adl = g["gorunen_ad"].lower() + " "
        g["cok_brans"] = [k for k in COK_BRANS_KELIMELER if k in adl]
        mes = [r["_mesafe_m"] for r in g["izinler"] if r["_mesafe_m"] is not None]
        g["en_yakin_m"] = min(mes) if mes else None
    return f


def eleme(firmalar, maks_ort):
    kalan, elenen = {}, []
    for key, g in firmalar.items():
        adl = g["gorunen_ad"].lower()
        vuran = [k for k in ELEME_KELIMELER if k in " " + adl + " "]
        if vuran:
            elenen.append((g, f"ad: '{vuran[0].strip()}' (low-voltage/yangın/mekanik/güneş/kablolama)"))
        elif g["ort"] is not None and g["ort"] > maks_ort:
            elenen.append((g, f"ortalama proje ${g['ort']:,.0f} > ${maks_ort:,.0f} (ticari ölçek)"))
        else:
            kalan[key] = g
    return kalan, elenen


def para(v):
    return VERI_YOK if v is None else f"${v:,.0f}"


def yaz(cikti, ctx, kalan, elenen):
    os.makedirs(cikti, exist_ok=True)
    # Sıralama: önce bizim ölçekteki ($3k–$25k) izin sayısı, sonra toplam izin.
    sirali = sorted(kalan.values(), key=lambda g: (-g["band"], -g["n"]))
    csv_yol = os.path.join(cikti, "elektrikci-adaylar-v2.csv")
    with open(csv_yol, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["sirket_adi", "varyantlar", "izin_sayisi", "band_3k_25k_izin",
                    "medyan_maliyet", "ort_proje_maliyeti", "min_maliyet", "maks_maliyet",
                    "maliyet_kayit_sayisi", "cok_bransli_etiket", "en_yakin_izin_m",
                    "ornek_permitnum", "ornek_adres", "ornek_sinif", "ornek_link", "son_izin_tarihi"])
        for g in sirali:
            o = g["izinler"][0]
            w.writerow([g["gorunen_ad"], " | ".join(sorted(g["varyantlar"])), g["n"], g["band"],
                        "" if g["medyan"] is None else round(g["medyan"]),
                        "" if g["ort"] is None else round(g["ort"]),
                        "" if g["min"] is None else round(g["min"]),
                        "" if g["max"] is None else round(g["max"]),
                        g["maliyet_kayit"], ",".join(g["cok_brans"]),
                        "" if g["en_yakin_m"] is None else round(g["en_yakin_m"]),
                        deger(o, "permitnum"), deger(o, "originaladdress1"),
                        deger(o, "permitclass"), deger(o, "link"),
                        deger(o, "issueddate")[:10]])

    L = ["# Konut elektrikçisi adayları v2", ""]
    L.append(f"- Üretim: {date.today().isoformat()}  ·  Kaynak: Seattle Open Data `c4tj-daue`")
    L.append(f"- **Taranan firma: {ctx['taranan']}**  ·  **Elenen: {len(elenen)}**  ·  "
             f"**Kalan aday: {len(kalan)}**")
    gerekce = defaultdict(int)
    for _, neden in elenen:
        gerekce["ad filtresi" if neden.startswith("ad:") else "ticari ölçek"] += 1
    L.append("- Eleme gerekçeleri: " + ", ".join(f"{k} {v}" for k, v in gerekce.items()) if elenen
             else "- Eleme gerekçeleri: (elenen yok)")
    L.append(f"- Kapsam: `issueddate > {ctx['baslangic']}`, sınıf ∈ {ctx['siniflar']}, "
             f"{ctx['yontem']}")
    L.append(f"- Yarıçap: {ctx['yaricap']} m" + (" (15 firmanın altında kaldığı için 8000'e çıkarıldı)"
                                                if ctx["genisletildi"] else ""))
    L.append(f"- Referans nokta: {ctx['ref_adres']} ({ctx['ref_lat']}, {ctx['ref_lon']}) — {ctx['ref_not']}")
    L.append(f"- Ölçek eşiği: ortalama proje maliyeti > ${ctx['maks_ort']:,.0f} → elendi; "
             f"maliyet alanı boşsa ortalama hesaplanmaz ve firma elenmez ('veri yok').")
    L.append("- Tekilleştirme: büyük harf; noktalama ve Inc/LLC/Corp/Co ekleri söküldü.")
    L.append("")
    L.append("## ADIM 1 — gerçek sınıf değerleri")
    L.append("")
    L.append("permitclassmapped:")
    L.append("")
    for r in ctx["mapped"]:
        L.append(f"- `{r.get('permitclassmapped')}`: {r.get('n')}")
    L.append("")
    L.append("permitclass (ilk 30):")
    L.append("")
    for r in ctx["cls"]:
        L.append(f"- `{r.get('permitclass')}`: {r.get('n')}")
    L.append("")
    L.append(f"Seçilen konut sınıfları: {ctx['siniflar']} ({ctx['sinif_kaynak']})")
    L.append("")
    L.append(f"## Adaylar (önce ${BAND_ALT:,.0f}–${BAND_UST:,.0f} bandındaki izin sayısı, sonra toplam)")
    L.append("")
    L.append("'band' = bizim ölçekteki izin sayısı; 'medyan' tek uç değerden etkilenmez "
             "(ortalamayı bozan veri hataları için). 'çok-branşlı' = adında ısıtma/tesisat/"
             "inşaat geçiyor, elenmedi ama saf elektrikçi değil.")
    L.append("")
    L.append("| # | firma | izin | band | medyan | ort. | min–maks | en yakın | not | örnek izin | örnek adres | son izin |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, g in enumerate(sirali, 1):
        o = g["izinler"][0]
        yk = VERI_YOK if g["en_yakin_m"] is None else f"{g['en_yakin_m']/1000:.1f} km"
        notu = "çok-branşlı" if g["cok_brans"] else ""
        L.append(f"| {i} | {g['gorunen_ad']} | {g['n']} | {g['band']} | {para(g['medyan'])} | "
                 f"{para(g['ort'])} | {para(g['min'])}–{para(g['max'])} | {yk} | {notu} | "
                 f"`{deger(o,'permitnum')}` | {deger(o,'originaladdress1')} | "
                 f"{deger(o,'issueddate')[:10]} |")
    if not sirali:
        L.append("| – | (aday kalmadı) | | | | | | | | | | |")
    L.append("")
    L.append("## Elendi")
    L.append("")
    L.append("| firma | izin | ort. maliyet | gerekçe |")
    L.append("|---|---|---|---|")
    for g, neden in sorted(elenen, key=lambda x: -x[0]["n"]):
        L.append(f"| {g['gorunen_ad']} | {g['n']} | {para(g['ort'])} | {neden} |")
    if not elenen:
        L.append("| – | | | |")
    L.append("")
    L.append("## Tekilleştirilen ad varyantları")
    L.append("")
    cok = [g for g in list(kalan.values()) + [e[0] for e in elenen] if len(g["varyantlar"]) > 1]
    for g in sorted(cok, key=lambda g: -g["n"]):
        L.append(f"- **{g['gorunen_ad']}** ← " + " · ".join(f"{k} ({v})" for k, v in sorted(g["varyantlar"].items())))
    if not cok:
        L.append("(varyant yok)")
    L.append("")
    md_yol = os.path.join(cikti, "ozet-v2.md")
    with open(md_yol, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    return csv_yol, md_yol


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yaricap", type=int, default=3200, help="metre (3200 ≈ 2 mil)")
    ap.add_argument("--baslangic", default="2023-09-01")
    ap.add_argument("--cikti", default=os.path.expanduser("~/mutfak"))
    ap.add_argument("--sinif", action="append", help="permitclassmapped konut değeri (tekrarlanabilir)")
    ap.add_argument("--maks-ort-maliyet", type=float, default=200000)
    ap.add_argument("--zipsiz", action="store_true", help="yarıçap filtresi olmadan tüm Seattle")
    ref = ap.add_argument_group("referans nokta (üçünden biri: sokak+zip+no | lat+lon | --zipsiz)")
    ref.add_argument("--sokak", help="evinizin sokağı, izin verisindeki yazımla, örn. 'PINE ST'")
    ref.add_argument("--zip", help="evinizin ZIP kodu, örn. 98122")
    ref.add_argument("--no", type=int, help="kapı numarası, örn. 1500")
    ref.add_argument("--lat", type=float); ref.add_argument("--lon", type=float)
    ap.add_argument("--base", default=RESOURCE, help=argparse.SUPPRESS)
    ap.add_argument("--schema-url", default=SCHEMA_URL, help=argparse.SUPPRESS)
    a = ap.parse_args()

    if not a.zipsiz and (a.lat is None or a.lon is None) and not (a.sokak and a.zip and a.no):
        ap.error("referans nokta gerekli: --sokak 'X AVE' --zip 98xxx --no 123  "
                 "ya da --lat/--lon  ya da --zipsiz")

    mapped, cls, siniflar, sinif_kaynak = adim1_siniflar(a.base, a.sinif)
    nokta, _ = nokta_kolonu(a.schema_url)
    print(f"  nokta kolonu: {nokta or 'yok (istemci tarafı yarıçap)'}", file=sys.stderr)
    if a.lat is not None and a.lon is not None:
        ref_adres, lat, lon, ref_not = "elle verildi", a.lat, a.lon, "--lat/--lon"
    elif a.zipsiz and not (a.sokak and a.zip and a.no):
        ref_adres, lat, lon, ref_not = "yok", 47.6062, -122.3321, "--zipsiz: mesafe Seattle merkezine göre, filtre değil"
    else:
        ref_adres, lat, lon, ref_not, _ = adim2_referans(a.base, a.sokak, a.zip, a.no)

    yaricap, genisletildi = a.yaricap, False
    rows, yontem = adim3_cek(a.base, nokta, lat, lon, yaricap, siniflar, a.baslangic, a.zipsiz)
    firmalar = firma_topla(rows)
    if not a.zipsiz and len(firmalar) < 15 and yaricap < 8000:
        print(f"  {len(firmalar)} firma < 15 → yarıçap 8000 m", file=sys.stderr)
        yaricap, genisletildi = 8000, True
        rows, yontem = adim3_cek(a.base, nokta, lat, lon, yaricap, siniflar, a.baslangic, False)
        firmalar = firma_topla(rows)
    print(f"ADIM 4/5/6 — {len(rows)} izin, {len(firmalar)} tekil firma", file=sys.stderr)
    kalan, elenen = eleme(firmalar, a.maks_ort_maliyet)
    ctx = dict(taranan=len(firmalar), baslangic=a.baslangic, siniflar=siniflar,
               sinif_kaynak=sinif_kaynak, yontem=yontem, yaricap=yaricap,
               genisletildi=genisletildi, ref_adres=ref_adres, ref_lat=lat, ref_lon=lon,
               ref_not=ref_not, maks_ort=a.maks_ort_maliyet, mapped=mapped, cls=cls)
    csv_yol, md_yol = yaz(a.cikti, ctx, kalan, elenen)
    print(f"\nTAMAM: {len(firmalar)} tarandı, {len(elenen)} elendi, {len(kalan)} aday\n"
          f"  {csv_yol}\n  {md_yol}", file=sys.stderr)


if __name__ == "__main__":
    main()
