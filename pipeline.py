"""
AI Shorts Generator: metinden otomatik dikey video (YouTube Shorts / TikTok / Reels)
edge-tts (yapay zeka sesi) + Pixabay/Pexels (stok video) + ffmpeg (kurgu + altyazı)

Tek ortak kod, birden fazla niş (kanal):
    niches/<niş>/settings.json   ses, hız, etiketler, renkler, yedek aramalar
    niches/<niş>/topics.json     o nişin senaryoları
    niches/<niş>/muzik/          o nişe özel müzikler (boşsa ortak muzik/ klasörü kullanılır)
    output/<niş>/                üretilen videolar

Özellikler:
  - Kelime zamanlamalı ücretsiz yapay zeka seslendirme
  - Anahtar kelimeye göre stok video, bulunamazsa yedek aramalar
  - Kelime kelime altyazı; sayılar ve "highlight" kelimeleri vurgulu
  - Key yoksa: hareketli gradyan (+ istenirse kayan yıldız alanı) arka plan
  - Arka plan müziği anlatıma göre seviyelenir, toplam ses YouTube standardı -14 LUFS

Kullanım:
    python pipeline.py                 # tüm nişleri üretir
    python pipeline.py space           # sadece bir niş (birden fazla da yazılabilir)
    python pipeline.py --list          # nişleri ve video sayılarını listeler
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
NICHES = HERE / "niches"
SHARED_MUSIC = HERE / "muzik"
OUT_ROOT = HERE / "output"
CACHE = HERE / "cache"
W, H, FPS = 1080, 1920, 30
TARGET_LUFS = -14            # YouTube'un ses standardı; sessiz videoları YouTube açmaz

# settings.json'da olmayan alanlar için varsayılanlar
DEFAULTS = {
    "voice": "en-US-AndrewNeural",
    "rate": "+5%",
    "fallback_queries": ["abstract background"],
    "default_tags": "#facts #shorts",
    "highlight_color": "#FFD60A",
    "starfield": False,
    "palettes": [["0x0f0c29", "0x302b63", "0x24243e"], ["0x141e30", "0x243b55", "0x0b0b13"]],
    "music": True,               # false: müzik eklenmez
    "scene_seconds": 3.7,        # sahne (klip) değişim süresi
    "first_scene_seconds": 0,    # >0: ilk kesme bu saniyede (giriş cümlesi bitince)
    "music_rel_db": -13,
}


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


# ---------------------------------------------------------------- yardımcılar

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


def lufs(inputs, graph=None):
    """Ses seviyesini (LUFS) ölçer. graph verilirse önce o filtre zinciri uygulanır ([a] çıkışlı)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats"] + inputs
    if graph:
        cmd += ["-filter_complex", graph.replace("[a]", ",ebur128[a]"), "-map", "[a]"]
    else:
        cmd += ["-af", "ebur128"]
    r = subprocess.run(cmd + ["-f", "null", "-"], capture_output=True, text=True)
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(found[-1]) if found else -70.0


# ---------------------------------------------------------------- nişler

class Niche:
    def __init__(self, folder):
        self.name = folder.name
        self.dir = folder
        cfg = dict(DEFAULTS)
        sf = folder / "settings.json"
        if sf.exists():
            cfg.update(json.loads(sf.read_text(encoding="utf-8")))
        self.cfg = cfg
        self.topics = json.loads((folder / "topics.json").read_text(encoding="utf-8"))
        self.out = OUT_ROOT / self.name

    def music_dirs(self):
        return [self.dir / "muzik", SHARED_MUSIC]

    def music_tracks(self):
        """Önce nişin kendi muzik klasörü; boşsa ortak muzik klasörü."""
        for d in self.music_dirs():
            if d.exists():
                tracks = sorted(p for p in d.iterdir() if p.suffix.lower() in (".mp3", ".wav", ".m4a"))
                if tracks:
                    return tracks
        return []


def find_niches(names):
    if not NICHES.exists():
        sys.exit("niches klasörü bulunamadı.")
    all_n = sorted(p for p in NICHES.iterdir() if p.is_dir() and (p / "topics.json").exists())
    if names:
        missing = [n for n in names if not (NICHES / n / "topics.json").exists()]
        if missing:
            sys.exit(f"Niş bulunamadı: {', '.join(missing)}. Mevcut: {', '.join(p.name for p in all_n)}")
        all_n = [NICHES / n for n in names]
    return [Niche(p) for p in all_n]


def migrate_legacy_output(niche):
    """Eski sürümde videolar doğrudan output/ içindeydi; ilgili nişin klasörüne taşır."""
    for t in niche.topics:
        for ext in (".mp4", ".mp3", ".txt"):
            old = OUT_ROOT / f"{t['slug']}{ext}"
            new = niche.out / f"{t['slug']}{ext}"
            if old.exists() and not new.exists():
                niche.out.mkdir(parents=True, exist_ok=True)
                old.rename(new)


# ---------------------------------------------------------------- ses

async def tts(text, mp3_path, voice, rate):
    """Sesi üretir, kelime zamanlamalarını döndürür: [(start, end, word), ...]"""
    com = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    words = []
    with open(mp3_path, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                start = ch["offset"] / 1e7
                words.append((start, start + ch["duration"] / 1e7, ch["text"]))
    return words


def quiet_intro_end(path):
    """Parçanın başındaki sessiz/çok kısık girişi atlamak için başlangıç saniyesi."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-t", "90", "-i", str(path),
                        "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True)
    pts = [(float(t), float(m)) for t, m in re.findall(r"t:\s*([\d.]+)\s+TARGET.*?M:\s*(-?[\d.]+)", r.stderr)]
    if not pts:
        return 0.0
    threshold = max(m for _, m in pts) - 12   # parçanın en güçlü yerine 12 dB yaklaştığı an
    first = next(t for t, m in pts if m >= threshold)
    return max(first - 2, 0.0)               # müzik yükselmeden 2 sn önce başla


def pick_music(niche, t):
    """topics.json'daki "music" alanını kullanır; yoksa klasörden konuya sabit bir parça seçer."""
    if not niche.cfg["music"]:
        return None, 0.0
    if t.get("music"):
        for d in niche.music_dirs():
            p = d / t["music"]
            if p.exists():
                return p, float(t.get("music_start", quiet_intro_end(p)))
        print(f"  uyarı: müzik bulunamadı ({t['music']}), klasörden seçiliyor")
    tracks = niche.music_tracks()
    if not tracks:
        return None, 0.0
    p = random.Random(t["slug"]).choice(tracks)
    return p, quiet_intro_end(p)


def audio_graph(vi, mi, total, mgain, norm=None):
    """Anlatım (+ varsa müzik) ses zinciri; çıkış etiketi [a].
    Müzik: anlatıma göre seviyelenir, yumuşak girip çıkar. norm verilirse toplam ses TARGET_LUFS'a çekilir."""
    if mi is None:
        g = f"[{vi}:a]apad,atrim=0:{total:.3f}"
    else:
        g = (f"[{mi}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={mgain:.2f}dB,"
             f"afade=t=in:d=0.6,afade=t=out:st={max(total - 1.2, 0):.3f}:d=1.2[bg];"
             f"[{vi}:a][bg]amix=inputs=2:duration=longest:normalize=0")
    if norm is not None:
        g += f",volume={norm:.2f}dB,alimiter=limit=0.89:level=false"
    return g + "[a]"


def audio_levels(voice, music, music_start, total, rel_db):
    """Müziğin kazancını ve son ses normalizasyonunu (dB) hesaplar."""
    v = lufs(["-i", str(voice)])
    if not music:
        return 0.0, TARGET_LUFS - v
    m = lufs(["-ss", f"{music_start:.2f}", "-t", f"{total:.2f}", "-i", str(music)])
    mgain = v + rel_db - m
    mixed = lufs(["-i", str(voice), "-ss", f"{music_start:.2f}", "-stream_loop", "-1", "-i", str(music)],
                 audio_graph(0, 1, total, mgain))
    return mgain, TARGET_LUFS - mixed


# ---------------------------------------------------------------- görsel

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


# ---------------------------------------------------------------- stok video

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


def fetch_clips(query, fallback_queries, n=4):
    """Önce konunun anahtar kelimesi, sonra nişin yedek aramaları. Hiçbiri olmazsa [] (gradyan).
    keywords bir liste ise klipler o aramalardan sırayla karıştırılır (ör. ["paper", "moon"])."""
    if isinstance(query, list):
        groups = [fetch_clips(q, [], -(-n // len(query))) for q in query]
        mixed = [c for i in range(n) for g in groups if i < len(g) for c in [g[i]]][:n]
        return mixed or fetch_clips(query[0], fallback_queries, n)
    sources = [fn for k, fn in ((PIXABAY_KEY, fetch_pixabay), (PEXELS_KEY, fetch_pexels)) if k]
    for q in [query] + list(fallback_queries):
        for fn in sources:
            try:
                paths = fn(q, n)
                if paths:
                    if q != query:
                        print(f"  '{query}' için klip yok, '{q}' kullanıldı")
                    return paths
            except Exception as e:
                print(f"  uyarı: {fn.__name__} başarısız ({e})")
    return []


# ---------------------------------------------------------------- kurgu

def scene_durations(total, cfg):
    """Sahne süreleri: istenirse kısa bir ilk sahne (giriş), kalanı scene_seconds'a yakın eşit parçalar."""
    first = cfg["first_scene_seconds"]
    if not first or first >= total - 1:
        n = max(1, round(total / cfg["scene_seconds"]))
        return [total / n] * n
    rest = total - first
    n = max(1, round(rest / cfg["scene_seconds"]))
    return [first] + [rest / n] * n


def render(niche, slug, total, clips, words, highlights, music=None, music_start=0.0):
    out, cfg = niche.out, niche.cfg
    use_gradient = not clips
    durs = scene_durations(total, cfg)
    n = len(durs)
    seg = max(durs)
    palette = random.choice(cfg["palettes"])
    segs = []
    stars = None
    drift = 10  # px/sn yıldız kayma hızı
    if use_gradient and cfg["starfield"]:
        stars = out / f"{slug}_stars.png"
        make_starfield(stars, W + int(seg * drift) + 20, H)
    for i in range(n):
        s = out / f"{slug}_seg{i}.mp4"
        seg = durs[i]
        if use_gradient:
            c0, c1, c2 = palette[i % 3], palette[(i + 1) % 3], palette[(i + 2) % 3]
            src = (f"gradients=size={W}x{H}:rate={FPS}:duration={seg:.3f}:speed=0.03:"
                   f"nb_colors=3:c0={c0}:c1={c1}:c2={c2}:seed={random.randint(1, 9999)}")
            if stars:
                cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-loop", "1", "-i", str(stars),
                       "-filter_complex", f"[0:v][1:v]overlay=x='-t*{drift}':y=0:shortest=1,format=yuv420p",
                       "-t", f"{seg:.3f}", "-r", str(FPS),
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(s)]
            else:
                cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-t", f"{seg:.3f}",
                       "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(s)]
        else:
            vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  f"fps={FPS},setsar=1,eq=brightness=-0.08")
            # klip sayısı yetmezse aynı klip farklı bir yerinden tekrar kullanılır
            clip = clips[i % len(clips)]
            offset = (i // len(clips)) * 4.0
            if offset:
                offset %= max(duration(clip) - 1, 1)
            cmd = ["ffmpeg", "-y", "-ss", f"{offset:.1f}", "-stream_loop", "-1", "-i", str(clip),
                   "-t", f"{seg:.3f}",
                   "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(s)]
        run(cmd)
        segs.append(s)
    lst = out / f"{slug}_list.txt"
    lst.write_text("".join(f"file '{s.name}'\n" for s in segs))
    pngs = []
    for i, (s, e, w) in enumerate(words):
        png = out / f"{slug}_w{i}.png"
        make_word_png(w, png, cfg["highlight_color"] if is_highlight(w, highlights) else "white")
        pngs.append(png)
    mgain, norm = audio_levels(out / f"{slug}.mp3", music, music_start, total, cfg["music_rel_db"])
    # cwd=out olduğu için dosya adları göreli (yol sorunlarını önler)
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-i", f"{slug}.mp3"]
    for png in pngs:
        cmd += ["-i", png.name]
    if music:
        cmd += ["-ss", f"{music_start:.2f}", "-stream_loop", "-1", "-i", str(Path(music).resolve())]
    chain, prev = [], "0:v"
    for i, (s, e, w) in enumerate(words):
        end = words[i + 1][0] if i + 1 < len(words) else e
        label = f"v{i + 1}"
        # yarı açık aralık: geçiş karesinde iki kelime üst üste binmesin
        chain.append(f"[{prev}][{i + 2}:v]overlay=(W-w)/2:(H-h)/2:"
                     f"enable='gte(t,{s:.3f})*lt(t,{end:.3f})'[{label}]")
        prev = label
    vmap = f"[{prev}]" if len(chain) else "0:v"
    chain.append(audio_graph(1, len(pngs) + 2 if music else None, total, mgain, norm))
    cmd += ["-filter_complex", ";".join(chain)]
    cmd += ["-map", vmap, "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-t", f"{total:.3f}", f"{slug}.mp4"]
    run(cmd, cwd=out)
    for f in segs + pngs + [lst] + ([stars] if stars else []):
        f.unlink()


# ---------------------------------------------------------------- ana akış

async def produce(niche):
    cfg = niche.cfg
    migrate_legacy_output(niche)
    niche.out.mkdir(parents=True, exist_ok=True)
    n_music = len(niche.music_tracks()) if cfg["music"] else 0
    print(f"\n=== {cfg.get('channel', niche.name)} ({niche.name}) — {len(niche.topics)} konu, "
          f"ses: {cfg['voice']}, müzik: {n_music} parça ===")
    made = 0
    for t in niche.topics:
        slug = t["slug"]
        if (niche.out / f"{slug}.mp4").exists():
            print(f"atlandı (zaten var): {slug}")
            continue
        print(f"üretiliyor: {slug}")
        mp3 = niche.out / f"{slug}.mp3"
        words = await tts(t["script"], mp3, cfg["voice"], cfg["rate"])
        total = duration(mp3) + 0.3
        clips = fetch_clips(t["keywords"], cfg["fallback_queries"], len(scene_durations(total, cfg)))
        if not clips:
            print("  stok klip yok, gradyan arka plan kullanılıyor")
        highlights = {clean(h) for h in t.get("highlight", [])}
        music, music_start = pick_music(niche, t)
        if music:
            print(f"  müzik: {music.name} ({music_start:.0f}. saniyeden)")
        render(niche, slug, total, clips, words, highlights, music, music_start)
        (niche.out / f"{slug}.txt").write_text(
            f"{t['title']}\n\n{t.get('description', '')}\n\n{t.get('tags', cfg['default_tags'])}",
            encoding="utf-8",
        )
        made += 1
        print(f"  hazır: output/{niche.name}/{slug}.mp4 ({total:.1f} sn)")
    return made


async def main():
    args = sys.argv[1:]
    if "--list" in args:
        for n in find_niches([]):
            done = sum((n.out / f"{t['slug']}.mp4").exists() for t in n.topics)
            print(f"{n.name:15s} {done}/{len(n.topics)} video hazır   ({n.cfg.get('channel', '')})")
        return
    niches = find_niches(args)
    mode = "Pixabay" if PIXABAY_KEY else ("Pexels" if PEXELS_KEY else "gradyan (key'siz)")
    print(f"Görsel kaynağı: {mode} | Nişler: {', '.join(n.name for n in niches)}")
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg bulunamadı, önce kur.")
    CACHE.mkdir(exist_ok=True)
    total = 0
    for n in niches:
        total += await produce(n)
    print(f"\nToplam {total} yeni video üretildi.")


if __name__ == "__main__":
    asyncio.run(main())
