#!/usr/bin/env python3
"""Elektrikçi teklif karşılaştırması → ~/mutfak/teklif-karsilastirma.md

Kullanım:
    python3 teklif.py teklifler.json [--izin-ucreti 350] [--cikti ~/mutfak/teklif-karsilastirma.md]

Girdi: teklifler.json — liste; her teklif bir nesne (şablon: teklifler.ornek.json).
Bilinmeyen alan null bırakılır; tabloda ⚠ olur ve "şunu sor" listesine girer.
Veri uydurulmaz. Yalnız Python standart kütüphanesi.
"""
import argparse
import json
import os
import sys
from datetime import date

ALANLAR = [
    ("firma", "firma"),
    ("lisans_no", "lisans_no"),
    ("seattle_lisansi", "seattle_lisansi(E/H)"),
    ("izni_kim_cekiyor", "izni_kim_cekiyor"),
    ("toplam_fiyat", "toplam_fiyat"),
    ("fiyat_tipi", "fiyat_tipi(sabit/saatlik)"),
    ("izin_ucreti_dahil", "izin_ucreti_dahil(E/H)"),
    ("cover_inspection_tarihi", "cover_inspection_tarihi"),
    ("tahmini_sure_gun", "tahmini_sure_gun"),
    ("re_inspection_ucretini_kim_oder", "re_inspection_ucretini_kim_oder"),
    ("sigorta_bond", "sigorta_bond(E/H)"),
    ("garanti_suresi", "garanti_suresi"),
]

SORULAR = {
    "lisans_no": "WA L&I elektrik müteahhidi lisans numaranız nedir? (lni.wa.gov'da doğrulanacak)",
    "seattle_lisansi": "Seattle şehir içinde çalışma yetkiniz/kaydınız var mı?",
    "izni_kim_cekiyor": "Elektrik iznini (SDCI) siz mi çekiyorsunuz, ben mi?",
    "toplam_fiyat": "Yazılı toplam fiyat nedir?",
    "fiyat_tipi": "Fiyat sabit mi, saatlik mi? Saatlikse saat ücreti ve tahmini saat?",
    "izin_ucreti_dahil": "İzin ücreti fiyata dahil mi?",
    "cover_inspection_tarihi": "Cover inspection (duvar kapanmadan önceki denetim) hangi tarihte? Yazılı taahhüt?",
    "tahmini_sure_gun": "İş kaç iş günü sürer?",
    "re_inspection_ucretini_kim_oder": "Denetim geçmezse re-inspection ücretini kim öder?",
    "sigorta_bond": "Genel sorumluluk sigortası ve bond belgesi (COI) verir misiniz?",
    "garanti_suresi": "İşçilik garantisi kaç yıl? Yazılı mı?",
    "yazili_teklif": "Teklifi yazılı (e-posta/PDF) verir misiniz?",
    "pesin_yuzde": "Ödeme planı nedir? Peşin yüzde kaç?",
    "tahmini_saat": "Saatlik teklif için tahmini toplam saat nedir?",
}

EH = {True: "E", False: "H"}


def bos(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def hucre(v):
    if bos(v):
        return "⚠"
    if isinstance(v, bool):
        return EH[v]
    if isinstance(v, float):
        return f"{v:,.0f}"
    return str(v)


def para(v):
    return f"${v:,.0f}"


def duzeltilmis_fiyat(t, izin_ucreti):
    """Aynı tabana getir: sabit fiyat + izin ücreti (dahil değilse).
    Saatlik teklif: saat_ucreti × tahmini_saat. Hesaplanamıyorsa (None, neden)."""
    tip = (t.get("fiyat_tipi") or "").lower()
    if tip == "saatlik":
        su, ts = t.get("saat_ucreti"), t.get("tahmini_saat")
        if bos(su) or bos(ts):
            return None, "saatlik: saat ücreti/tahmini saat yok"
        taban = float(su) * float(ts)
        not_ = f"{su}$/sa × {ts} sa"
    elif tip == "sabit":
        if bos(t.get("toplam_fiyat")):
            return None, "toplam fiyat yok"
        taban = float(t["toplam_fiyat"])
        not_ = "sabit"
    else:
        return None, "fiyat tipi belirsiz"
    dahil = t.get("izin_ucreti_dahil")
    if dahil is None:
        return None, not_ + "; izin dahil mi belirsiz"
    if dahil is False:
        if izin_ucreti is None:
            return None, not_ + "; izin ücreti bilinmiyor (--izin-ucreti ver)"
        taban += izin_ucreti
        not_ += f" + izin {para(izin_ucreti)}"
    return taban, not_


def risk_puani(t):
    """Yüksek puan = yüksek risk. Her madde gerekçesiyle döner."""
    p, g = 0, []
    def ekle(n, s):
        nonlocal p
        p += n; g.append(f"+{n} {s}")
    if t.get("izni_kim_cekiyor") and "ev sahibi" in str(t["izni_kim_cekiyor"]).lower():
        ekle(4, "izni ev sahibine çektiriyor (sorumluluk size kalır)")
    if bos(t.get("izni_kim_cekiyor")): ekle(2, "izni kimin çektiği belirsiz")
    if t.get("yazili_teklif") is False: ekle(4, "yazılı teklif yok")
    if bos(t.get("yazili_teklif")): ekle(1, "yazılı teklif durumu belirsiz")
    if bos(t.get("cover_inspection_tarihi")): ekle(3, "cover inspection tarihi taahhüt edilmemiş")
    pes = t.get("pesin_yuzde")
    if not bos(pes) and float(pes) >= 50: ekle(3, f"peşin %{pes:g}")
    if bos(pes): ekle(1, "ödeme planı belirsiz")
    if t.get("sigorta_bond") is False: ekle(4, "sigorta/bond yok")
    if bos(t.get("sigorta_bond")): ekle(2, "sigorta/bond belgesi görülmedi")
    if bos(t.get("lisans_no")): ekle(3, "lisans numarası yok")
    if t.get("seattle_lisansi") is False: ekle(2, "Seattle yetkisi yok")
    if (t.get("fiyat_tipi") or "").lower() == "saatlik": ekle(2, "saatlik fiyat (açık uçlu maliyet)")
    rio = str(t.get("re_inspection_ucretini_kim_oder") or "").lower()
    if "ev sahibi" in rio or "müşteri" in rio: ekle(2, "re-inspection ücreti müşteriye")
    if bos(t.get("re_inspection_ucretini_kim_oder")): ekle(1, "re-inspection ücreti belirsiz")
    if bos(t.get("garanti_suresi")): ekle(1, "garanti belirsiz")
    if bos(t.get("tahmini_sure_gun")): ekle(1, "süre belirsiz")
    return p, g


def kirmizi_bayraklar(t):
    b = []
    if t.get("izni_kim_cekiyor") and "ev sahibi" in str(t["izni_kim_cekiyor"]).lower():
        b.append("izni ev sahibine çektirmek istiyor")
    if t.get("yazili_teklif") is False:
        b.append("yazılı teklif vermiyor")
    if bos(t.get("cover_inspection_tarihi")):
        b.append("cover inspection tarihini taahhüt etmiyor")
    pes = t.get("pesin_yuzde")
    if not bos(pes) and float(pes) >= 50:
        b.append(f"peşin %{float(pes):g} istiyor")
    return b


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("girdi", help="teklifler.json")
    ap.add_argument("--izin-ucreti", type=float, default=None,
                    help="SDCI elektrik izin ücreti (USD); dahil olmayan teklife eklenir")
    ap.add_argument("--cikti", default=os.path.expanduser("~/mutfak/teklif-karsilastirma.md"))
    a = ap.parse_args()

    with open(a.girdi, encoding="utf-8") as f:
        teklifler = json.load(f)
    if not isinstance(teklifler, list) or not teklifler:
        sys.exit("HATA: girdi boş ya da liste değil")

    L = [f"# Elektrikçi teklif karşılaştırması", "",
         f"- Tarih: {date.today().isoformat()}  ·  Teklif sayısı: {len(teklifler)}",
         "- ⚠ = teklifte bu bilgi yok; tahmin edilmedi, 'şunu sor' listesinde.",
         ""]
    if a.izin_ucreti is not None:
        L.append(f"- Taban düzeltmesinde kullanılan izin ücreti: {para(a.izin_ucreti)} (sizin verdiğiniz)")
        L.append("")

    # 1. Tablo
    L.append("## Karşılaştırma tablosu")
    L.append("")
    L.append("| " + " | ".join(b for _, b in ALANLAR) + " |")
    L.append("|" + "---|" * len(ALANLAR))
    for t in teklifler:
        L.append("| " + " | ".join(hucre(t.get(k)) for k, _ in ALANLAR) + " |")
    L.append("")

    # 2. Elma-armut düzeltmesi
    L.append("## Aynı tabana getirilmiş fiyat")
    L.append("")
    L.append("Taban = sabit fiyat + (izin ücreti dahil değilse izin ücreti); saatlik = saat ücreti × tahmini saat.")
    L.append("")
    L.append("| firma | teklif fiyatı | düzeltilmiş fiyat | nasıl |")
    L.append("|---|---|---|---|")
    duz = {}
    for t in teklifler:
        f_, n = duzeltilmis_fiyat(t, a.izin_ucreti)
        duz[t.get("firma")] = f_
        L.append(f"| {hucre(t.get('firma'))} | {hucre(t.get('toplam_fiyat'))} | "
                 f"{para(f_) if f_ is not None else '⚠ hesaplanamadı'} | {n} |")
    L.append("")

    # 3. Risk önerisi
    L.append("## Öneri: en az risk taşıyan")
    L.append("")
    skor = []
    for t in teklifler:
        p, g = risk_puani(t)
        skor.append((p, t, g))
    skor.sort(key=lambda x: (x[0], duz.get(x[1].get("firma")) or float("inf")))
    L.append("| firma | risk puanı (düşük iyi) | düzeltilmiş fiyat | risk gerekçeleri |")
    L.append("|---|---|---|---|")
    for p, t, g in skor:
        f_ = duz.get(t.get("firma"))
        L.append(f"| {hucre(t.get('firma'))} | {p} | {para(f_) if f_ is not None else '⚠'} | "
                 f"{'; '.join(g) if g else 'risk maddesi yok'} |")
    L.append("")
    en_az = skor[0]
    L.append(f"**Önerilen: {hucre(en_az[1].get('firma'))}** (risk puanı {en_az[0]})")
    L.append("")
    L.append("Gerekçe:")
    # Üç madde: öneri tekliflerinin diğerlerine göre güçlü olduğu noktalar
    t = en_az[1]
    maddeler = []
    if t.get("izni_kim_cekiyor") and "ev sahibi" not in str(t["izni_kim_cekiyor"]).lower():
        maddeler.append("İzni kendisi çekiyor: SDCI karşısında sorumluluk ve denetim takibi müteahhitte kalır.")
    if not bos(t.get("cover_inspection_tarihi")):
        maddeler.append(f"Cover inspection tarihini yazılı taahhüt ediyor ({t['cover_inspection_tarihi']}): duvar kapanmadan denetim garanti.")
    if t.get("sigorta_bond") is True and not bos(t.get("lisans_no")):
        maddeler.append(f"Lisans ({t['lisans_no']}) + sigorta/bond var: hasar ve eksik iş durumunda başvuru yolu açık.")
    if (t.get("fiyat_tipi") or "").lower() == "sabit" and t.get("izin_ucreti_dahil") is True:
        maddeler.append("Sabit fiyat ve izin dahil: sürpriz maliyet yok.")
    if t.get("yazili_teklif") is True:
        maddeler.append("Yazılı teklif: kapsam ve fiyat belgelenmiş.")
    if not bos(t.get("garanti_suresi")):
        maddeler.append(f"İşçilik garantisi: {t['garanti_suresi']}.")
    if len(maddeler) < 3:
        maddeler.append("⚠ Bu teklifte de eksik alanlar var; 'şunu sor' listesi kapanmadan karar verme.")
    for m in maddeler[:3]:
        L.append(f"1. {m}")
    L.append("")
    if en_az[0] > 0:
        L.append(f"Not: Önerilen teklifin de risk puanı {en_az[0]}; en düşük olması 'risksiz' demek değil.")
        L.append("")

    # 4. Kırmızı bayraklar
    L.append("## Kırmızı bayraklar")
    L.append("")
    var = False
    for t in teklifler:
        b = kirmizi_bayraklar(t)
        if b:
            var = True
            L.append(f"- **{hucre(t.get('firma'))}**: " + "; ".join(b))
    if not var:
        L.append("(hiçbir teklifte tanımlı kırmızı bayrak yok)")
    L.append("")

    # 1b. Şunu sor
    L.append("## Şunu sor (eksik bilgiler)")
    L.append("")
    for t in teklifler:
        eksik = [k for k, _ in ALANLAR if bos(t.get(k))]
        for ek in ("yazili_teklif", "pesin_yuzde"):
            if bos(t.get(ek)):
                eksik.append(ek)
        if (t.get("fiyat_tipi") or "").lower() == "saatlik" and bos(t.get("tahmini_saat")):
            eksik.append("tahmini_saat")
        if not eksik:
            continue
        L.append(f"### {hucre(t.get('firma'))}")
        for k in eksik:
            L.append(f"- [ ] {SORULAR.get(k, k)}")
        L.append("")

    os.makedirs(os.path.dirname(a.cikti), exist_ok=True)
    with open(a.cikti, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"TAMAM: {a.cikti}")


if __name__ == "__main__":
    main()
