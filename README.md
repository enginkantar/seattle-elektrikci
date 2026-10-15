# seattle-elektrikci

Seattle'da **ruhsatlı, izinle çalışan konut elektrikçisi** bulmak için açık veri
araçları. Belediyenin Building Permits veri setinden (`c4tj-daue`, SODA API,
kimlik gerekmez) evinizin çevresinde gerçekten konut işi yapan, sizin ölçeğinizde
çalışan ve son iki yılda aktif müteahhitleri çıkarır; gelen teklifleri tek
tabloda karşılaştırır.

*Open-data tools for finding licensed residential electricians in Seattle from the
city's Building Permits dataset. Turkish-first; contributions in any language welcome.*

Yalnız Python 3 standart kütüphanesi. `pip install` yok.

## Hızlı başlangıç

```sh
git clone https://github.com/<kullanici>/seattle-elektrikci.git
cd seattle-elektrikci

# Evinizin çevresi (sokak adı izin verisindeki yazımla, büyük harf)
python3 tara_v2.py --sokak "PINE ST" --zip 98122 --no 1500

# Sonuç zayıfsa tüm Seattle
python3 tara_v2.py --zipsiz

# Teklifler gelince
cp teklifler.ornek.json ~/mutfak/teklifler.json   # doldurun, bilinmeyen alan null
python3 teklif.py ~/mutfak/teklifler.json --izin-ucreti 350
```

Çıktılar `~/mutfak/` altına yazılır (`--cikti` ile değiştirin):
`ozet-v2.md`, `elektrikci-adaylar-v2.csv`, `teklif-karsilastirma.md`.

## Araçlar

| dosya | ne yapar |
|---|---|
| `tara_v2.py` | Konut sınıfı + yarıçap + proje ölçeği ile müteahhit taraması. Sınıf değerlerini veriden sorgular, referans noktayı izin kayıtlarından alır (geocoding yok), yangın/alarm/güneş/kablolama firmalarını ad filtresiyle eler, ad varyantlarını tekilleştirir. |
| `teklif.py` | Teklifleri 12 sütunlu tabloya koyar, eksik bilgiyi ⚠ işaretler ve "şunu sor" listesi çıkarır, izin ücreti/saatlik tabanını eşitler, **en az riskli** teklifi gerekçesiyle önerir, kırmızı bayrakları listeler. |
| `tara.py` | İlk sürüm: yalnız ZIP + "kitchen/remodel". Ticari ZIP'lerde yanlış pazarı yakalar; referans için duruyor. |

## Dürüst uyarılar

- `estprojectcost` alanında bozuk kayıtlar var; **medyana** bakın, ortalamaya değil.
- Ad filtresi bir sezgidir. "Elendi" tablosunu gözden geçirin.
- **Lisans doğrulaması araçta yok.** Her firmayı lni.wa.gov'da ("Verify a Contractor") kontrol edin.
- İzin sayısı aktiflik gösterir, işçilik kalitesi değil. Portaldaki denetim geçmişine bakın.
- Yalnız Seattle şehir sınırları. Bellevue, Tacoma, Shoreline bu veri setinde yok;
  başka şehir eklemek için `CONTRIBUTING.md`.

## Elektrikçiye ilk telefonda sorulacaklar

1. WA L&I lisans numaranız?
2. İzni kim çekiyor? **"Siz çekin" diyen elenir.**
3. Fiyat sabit mi, saatlik mi? İzin ücreti dahil mi?
4. Cover inspection tarihini yazılı taahhüt ediyor musunuz?
5. Denetim geçmezse re-inspection ücretini kim öder?
6. Sigorta ve bond belgesi (COI) verir misiniz?
7. İşçilik garantisi kaç yıl, yazılı mı?
8. Ödeme planı, peşin yüzde kaç?
9. Yazılı teklif ne zaman gelir?

Cevaplar doğrudan `teklif.py` sütunlarına gider.

## Veri kaynağı

- API: `https://data.seattle.gov/resource/c4tj-daue.json` (Socrata SODA 2.1)
- Şema: `https://cos-data.seattle.gov/api/views/c4tj-daue/columns.json`

## Lisans

MIT. Serbestçe kullanın, değiştirin, paylaşın.
