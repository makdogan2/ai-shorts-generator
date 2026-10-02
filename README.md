# Uzay Fabrikası 🚀

Uzay ve fizik konulu YouTube Shorts videolarını otomatik üreten Windows hattı.
Senaryoyu yazarsın, gerisini (seslendirme, stok video, kelime kelime altyazı, kurgu) o yapar.

**Kullanılanlar:** edge-tts (ücretsiz ses) · Pixabay/Pexels (stok video) · FFmpeg (kurgu) · Pillow (altyazı)

## Kurulum

1. `kurulum.bat` dosyasına çift tıkla (Python paketleri, FFmpeg ve Pixabay key).
2. `calistir.bat` dosyasına çift tıkla.

Videolar `output/` klasörüne düşer: her konu için bir `.mp4` ve başlık/açıklama/etiketleri içeren bir `.txt`.

## Özellikler

- Belgesel tonunda derin ses (`en-US-ChristopherNeural`)
- Altyazıda sayılar ve `highlight` kelimeleri sarı
- Pixabay key yoksa kayan yıldız alanlı uzay arka planı
- Konuya özel klip yoksa genel uzay kliplerine düşer
- Klasöre `muzik.mp3` koyarsan arkada kısık sesle çalar

## Yeni video eklemek

`topics.json` dosyasına yeni bir blok ekle:

```json
{
  "slug": "benzersiz-ad",
  "title": "Video başlığı",
  "script": "Seslendirilecek metin, yaklaşık 35-40 kelime.",
  "keywords": "moon",
  "highlight": ["Moon"],
  "description": "Kısa açıklama",
  "tags": "#space #science #shorts"
}
```

## Başka bir niş

`pipeline.py` dosyasının en üstündeki **Niş ayarları** bloğunu ve `topics.json` dosyasını değiştirmen yeterli.

> API key'ler `pixabay_key.txt` / `pexels_key.txt` dosyalarında durur ve `.gitignore` sayesinde repoya girmez.
