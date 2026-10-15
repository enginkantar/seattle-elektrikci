# Katkı rehberi

Hata bildirimi, filtre iyileştirmesi ve yeni şehir desteği hepsi hoş gelir.
Issue açın ya da doğrudan PR gönderin. Dil Türkçe ya da İngilizce, fark etmez.

## Kurallar

1. **Veri uydurma.** API bir alanı boş döndürüyorsa çıktıda `veri yok` yazılır,
   tahmin edilmez. Yeni bir sütun eklerken bu kuralı koruyun.
2. **Yalnız standart kütüphane.** `requests`, `pandas` vb. eklemeyin; hedef
   kitle Python kurulumu olmayan ev kullanıcısı.
3. **Sınıf değerlerini sorgula, varsayma.** `permitclassmapped` gibi alanların
   değerleri şehre göre değişir; `ADIM 1` bunları her koşumda çeker ve özete yazar.
4. Değişiklikten sonra en az bir canlı koşum yapın ve PR açıklamasına
   `TAMAM: N tarandı, M elendi, K aday` satırını yapıştırın.

## Filtre iyileştirmesi

- Ad filtresi `tara_v2.py` başındaki `ELEME_KELIMELER` (sert eleme) ve
  `COK_BRANS_KELIMELER` (yumuşak etiket) listeleridir. Bir firma haksız elendiyse
  ya da kaçtıysa buradan düzeltin, PR'da örnek firma adını yazın.
- Ölçek bandı `BAND_ALT`, `BAND_UST` (varsayılan $3k–$25k).
- Tekilleştirme `normalize()`; yeni bir şirket eki (örn. "L.P.") buradan eklenir.

## Yeni şehir eklemek

Birçok ABD şehri izin verisini Socrata (SODA) üzerinden yayınlar; sorgu sözdizimi
aynıdır (`$where`, `$group`, `within_circle`). Farklı olan **kolon adları** ve
**sınıf değerleri**dir. Yol:

1. Şehrin veri portalında building/electrical permits veri setini bulun;
   `https://<portal>/resource/<id>.json` adresini ve `columns.json` şemasını alın.
2. Şu kolonların karşılığını bulun: izin numarası, müteahhit adı, adres, ZIP,
   konut/ticari sınıfı, tahmini maliyet, veriliş tarihi, lat/lon ya da point kolonu.
3. `tara_v2.py` içinde bu adlar şimdilik sabit (`SATIR_KOLONLARI`, `$where`
   ifadeleri). Önerilen yapı: `sehirler/<sehir>.json` dosyasında kolon eşlemesi,
   `--sehir <ad>` ile seçim. Bu refactor henüz yapılmadı; ilk PR için iyi bir konu.
4. Referans nokta adımı adres metninde kapı numarası arar; şehrin adres biçimi
   farklıysa `adim2_referans()` uyarlanır.
5. README'ye şehri, veri seti kimliğini ve bilinen tuhaflıkları (bozuk alanlar,
   eksik lat/lon oranı) ekleyin.

Tacoma, Bellevue ve King County için veri seti bulan olursa issue açsın;
kolon eşlemesini birlikte çıkarırız.

## Test

Canlı API dışında test için küçük bir sahte SODA sunucusu yeterli: `$where`
içinden `within_circle`, `IN (...)`, `like`, `issueddate >` ifadelerini ayrıştırıp
sabit satır listesini filtreleyen 60 satırlık bir `http.server`. Repoya
`tests/mock_soda.py` olarak eklenmesi açık iştir.
