"""
Uzay Fabrikası: uzay & fizik Shorts üretim hattı
edge-tts (ses) + Pixabay/Pexels (stok video) + ffmpeg (kurgu + altyazı)

Farklar (psikoloji sürümüne göre):
  - Belgesel tonunda derin ses
  - Key yoksa: hareketli gradyan + kayan yıldız alanı arka planı
  - Altyazıda sayılar ve topics.json'daki "highlight" kelimeleri sarı
  - Konuya özel klip bulunamazsa önce genel uzay klipleri denenir
  - Klasöre muzik.mp3 koyarsan arkada kısık sesle çalar (opsiyonel)

Kullanım:
    python pipeline.py            # topics.json içindeki tüm videoları üretir
"""
import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

import edge_tts
import requests
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent


def _key(env_name, file_name):
    """Key'i ortam değişkeninden ya da yanındaki txt dosyasından okur; boşluk ve tırnakları temizler."""
    v = os.environ.get(env_name)
    f = HERE / file_name
    if not v and f.exists():
        v = f.read_text(encoding="utf-8")
    v = (v or "").strip().strip('"').strip("'").strip()
    return v or None


PIXABAY_KEY = _key("PIXABAY_API_KEY", "pixabay_key.txt")
PEXELS_KEY = _key("PEXELS_API_KEY", "pexels_key.txt")

# --- Niş ayarları (başka bir niş için sadece bu bloğu ve topics.json'u değiştir) ---
VOICE = "en-US-ChristopherNeural"   # alternatifler: en-GB-RyanNeural, en-US-AndrewNeural, en-US-GuyNeural
RATE = "+5%"
FALLBACK_QUERIES = ["galaxy", "stars night sky", "space"]   # konuya özel klip yoksa denenir
DEFAULT_TAGS = "#space #science #shorts"
HIGHLIGHT = "#FFD60A"               # vurgulu kelime rengi (sarı)
PALETTES = [                        # key yokken gradyan renkleri (derin uzay tonları)
    ("0x05061a", "0x1b1464", "0x0a0a0f"),
    ("0x000814", "0x003566", "0x0b132b"),
    ("0x10002b", "0x3c096c", "0x05000f"),
    ("0x03071e", "0x1d3557", "0x000000"),
]
MUSIC_FILE = HERE / "muzik.mp3"
MUSIC_VOLUME = 0.12
# ------------------------------------------------------------------------------------

W, H, FPS = 1080, 1920, 30
OUT = Path("output")
CACHE = Path("cache")


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        print("\nFFmpeg hatası (son satırlar):")
        print("\n".join(r.stderr.strip().splitlines()[-12:]))
        raise SystemExit(1)


def duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


async def tts(text, mp3_path):
    """Sesi üretir, kelime zamanlamalarını döndürür: [(start, end, word), ...]"""
    com = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
    words = []
    with open(mp3_path, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                start = ch["offset"] / 1e7
                words.append((start, start + ch["duration"] / 1e7, ch["text"]))
    return words


FONT_PATHS = [
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def get_font(size):
    for fp in FONT_PATHS:
        if Path(fp).exists():
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default(size)


def clean(word):
    return re.sub(r"[^\w]", "", word).lower()


def is_highlight(word, highlights):
    return any(ch.isdigit() for ch in word) or clean(word) in highlights


def make_word_png(word, path, color="white"):
    """Tek kelimelik, şeffaf zeminli, renkli yazı + siyah kontur PNG."""
    text = word.upper()
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    size = 140
    while True:
        font = get_font(size)
        bbox = probe.textbbox((0, 0), text, font=font, stroke_width=10)
        w, h = bbox[2] - bbox[0] + 40, bbox[3] - bbox[1] + 40
        if w <= 1000 or size <= 60:
            break
        size -= 10
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((20 - bbox[0], 20 - bbox[1]), text, font=font,
                             fill=color, stroke_width=10, stroke_fill="black")
    img.save(path)


def make_starfield(path, width, height):
    """Şeffaf zeminli yıldız alanı: çoğu küçük, birkaçı parlak ve hafif ışıltılı."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for _ in range(900):
        x, y = random.randint(0, width - 1), random.randint(0, height - 1)
        a = random.randint(60, 220)
        tint = random.choice([(255, 255, 255), (200, 220, 255), (255, 240, 220)])
        r = random.choice([0, 0, 0, 1, 1, 2])
        d.ellipse((x - r, y - r, x + r, y + r), fill=(*tint, a))
    for _ in range(25):
        x, y = random.randint(0, width - 1), random.randint(0, height - 1)
        for r, a in ((9, 25), (5, 60), (2, 255)):
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, a))
    img.save(path)


def fetch_pexels(query, n=4):
    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": PEXELS_KEY},
        params={"query": query, "orientation": "portrait", "per_page": 15},
        timeout=30,
    )
    r.raise_for_status()
    vids = r.json().get("videos", [])
    random.shuffle(vids)
    paths = []
    for v in vids:
        files = [f for f in v["video_files"] if f["height"] >= 1280 and f["width"] < f["height"]]
        if not files:
            continue
        f = min(files, key=lambda x: x["height"])
        p = CACHE / f"pexels_{v['id']}.mp4"
        if not p.exists():
            p.write_bytes(requests.get(f["link"], timeout=120).content)
        paths.append(p)
        if len(paths) == n:
            break
    return paths


def fetch_pixabay(query, n=4):
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": PIXABAY_KEY, "q": query, "per_page": 20, "safesearch": "true"},
        timeout=30,
    )
    r.raise_for_status()
    hits = r.json().get("hits", [])
    random.shuffle(hits)
    paths = []
    for h in hits:
        vids = h.get("videos", {})
        # en kaliteliden başla; boş url'leri atla
        v = next((vids[k] for k in ("large", "medium", "small") if vids.get(k, {}).get("url")), None)
        if not v:
            continue
        p = CACHE / f"pixabay_{h['id']}.mp4"
        if not p.exists():
            p.write_bytes(requests.get(v["url"], timeout=120).content)
        paths.append(p)
        if len(paths) == n:
            break
    return paths


def fetch_clips(query, n=4):
    """Önce konunun anahtar kelimesi, sonra genel uzay aramaları. Hiçbiri olmazsa [] (yıldızlı gradyan)."""
    sources = [(k, fn) for k, fn in ((PIXABAY_KEY, fetch_pixabay), (PEXELS_KEY, fetch_pexels)) if k]
    for q in [query] + FALLBACK_QUERIES:
        for _, fn in sources:
            try:
                paths = fn(q, n)
                if paths:
                    if q != query:
                        print(f"  '{query}' için klip yok, '{q}' kullanıldı")
                    return paths
            except Exception as e:
                print(f"  uyarı: {fn.__name__} başarısız ({e})")
    return []


def render(slug, total, clips, words, highlights):
    use_gradient = not clips
    n = 3 if use_gradient else len(clips)
    seg = total / n
    palette = random.choice(PALETTES)
    segs = []
    stars = None
    if use_gradient:
        drift = 10  # px/sn yıldız kayma hızı
        stars = OUT / f"{slug}_stars.png"
        make_starfield(stars, W + int(seg * drift) + 20, H)
    for i in range(n):
        s = OUT / f"{slug}_seg{i}.mp4"
        if use_gradient:
            c0, c1, c2 = palette[i % 3], palette[(i + 1) % 3], palette[(i + 2) % 3]
            src = (f"gradients=size={W}x{H}:rate={FPS}:duration={seg:.3f}:speed=0.03:"
                   f"nb_colors=3:c0={c0}:c1={c1}:c2={c2}:seed={random.randint(1, 9999)}")
            cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-loop", "1", "-i", str(stars),
                   "-filter_complex", f"[0:v][1:v]overlay=x='-t*{drift}':y=0:shortest=1,format=yuv420p",
                   "-t", f"{seg:.3f}", "-r", str(FPS),
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(s)]
        else:
            vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  f"fps={FPS},setsar=1,eq=brightness=-0.08")
            cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(clips[i]), "-t", f"{seg:.3f}",
                   "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(s)]
        run(cmd)
        segs.append(s)
    lst = OUT / f"{slug}_list.txt"
    lst.write_text("".join(f"file '{s.name}'\n" for s in segs))
    pngs = []
    for i, (s, e, w) in enumerate(words):
        png = OUT / f"{slug}_w{i}.png"
        make_word_png(w, png, HIGHLIGHT if is_highlight(w, highlights) else "white")
        pngs.append(png)
    # cwd=OUT olduğu için dosya adları göreli (yol sorunlarını önler)
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-i", f"{slug}.mp3"]
    for png in pngs:
        cmd += ["-i", png.name]
    music = MUSIC_FILE.exists()
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(MUSIC_FILE)]
    chain, prev = [], "0:v"
    for i, (s, e, w) in enumerate(words):
        end = words[i + 1][0] if i + 1 < len(words) else e
        label = f"v{i + 1}"
        # yarı açık aralık: geçiş karesinde iki kelime üst üste binmesin
        chain.append(f"[{prev}][{i + 2}:v]overlay=(W-w)/2:(H-h)/2:"
                     f"enable='gte(t,{s:.3f})*lt(t,{end:.3f})'[{label}]")
        prev = label
    vmap = f"[{prev}]" if chain else "0:v"
    amap = "1:a"
    if music:
        m = len(pngs) + 2
        # amix sesleri ikiye böler; sonundaki volume=2 bunu telafi eder
        chain.append(f"[{m}:a]volume={MUSIC_VOLUME}[bg];"
                     f"[1:a][bg]amix=inputs=2:duration=first:dropout_transition=0,volume=2[aout]")
        amap = "[aout]"
    if chain:
        cmd += ["-filter_complex", ";".join(chain)]
    cmd += ["-map", vmap, "-map", amap, "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", f"{total:.3f}", f"{slug}.mp4"]
    run(cmd, cwd=OUT)
    for f in segs + pngs + [lst] + ([stars] if stars else []):
        f.unlink()


async def main():
    mode = "Pixabay" if PIXABAY_KEY else ("Pexels" if PEXELS_KEY else "yıldızlı gradyan (key'siz)")
    print(f"Görsel kaynağı: {mode}")
    if MUSIC_FILE.exists():
        print("Arka plan müziği: muzik.mp3")
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg bulunamadı, önce kur.")
    OUT.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True)

    topics = json.loads(Path("topics.json").read_text(encoding="utf-8"))
    for t in topics:
        slug = t["slug"]
        if (OUT / f"{slug}.mp4").exists():
            print(f"atlandı (zaten var): {slug}")
            continue
        print(f"üretiliyor: {slug}")
        mp3 = OUT / f"{slug}.mp3"
        words = await tts(t["script"], mp3)
        total = duration(mp3) + 0.3
        clips = fetch_clips(t["keywords"])
        if not clips:
            print("  stok klip yok, yıldızlı gradyan arka plan kullanılıyor")
        highlights = {clean(h) for h in t.get("highlight", [])}
        render(slug, total, clips, words, highlights)
        (OUT / f"{slug}.txt").write_text(
            f"{t['title']}\n\n{t.get('description', '')}\n\n{t.get('tags', DEFAULT_TAGS)}",
            encoding="utf-8",
        )
        print(f"  hazır: output/{slug}.mp4 ({total:.1f} sn)")


if __name__ == "__main__":
    asyncio.run(main())
