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
  - Her cümleye kendi stok videosu ("visuals"), yoksa anahtar kelimeye göre; bulunamazsa yedek aramalar
  - Kelime kelime, zıplayarak gelen altyazı; sayılar ve "highlight" kelimeleri vurgulu; üstte ilerleme çubuğu
  - Her videodan sonra otomatik kalite kontrolü (altyazı, ses seviyesi, süre); bozuksa yeniden üretir
  - Key yoksa: hareketli gradyan (+ istenirse kayan yıldız alanı) arka plan
  - Arka plan müziği anlatıma göre seviyelenir, toplam ses YouTube standardı -14 LUFS

Kullanım:
    python pipeline.py                 # tüm nişleri üretir
    python pipeline.py space           # sadece bir niş (birden fazla da yazılabilir)
    python pipeline.py --list          # nişleri ve video sayılarını listeler
    python pipeline.py --check space   # hazır videoları kalite kontrolünden geçirir
"""
import asyncio
import functools
import json
import math
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
# Her sahne aynı piksel/renk ayarlarıyla kodlanır. Klipler farklı renk etiketleriyle gelince (ör. biri bt709,
# biri etiketsiz) FFmpeg 7+ birleştirmede filtreleri baştan kuruyor ve altyazılar o sahneden sonra kayboluyordu.
SEG_NORM = "format=yuv420p,setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
TARGET_LUFS = -14            # YouTube'un ses standardı; sessiz videoları YouTube açmaz

# settings.json'da olmayan alanlar için varsayılanlar
DEFAULTS = {
    "voice": "en-US-AndrewNeural",
    "lang": "en",                # "tr": Türkçe büyük harf kuralları (i -> İ)
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
    "music_fade_in": 0.6,        # 0: müzik ilk karede tam enerjiyle başlar
    "sfx": False,                # true: sahne geçişlerinde whoosh, ilk kesmede boom
    "sfx_rel_db": -8,            # efektlerin anlatıma göre seviyesi (dB)
    "caption_pop": True,         # kelimeler küçük bir büyüme efektiyle ekrana gelir
    "progress_bar": "top",       # "top" | "bottom" | false: videonun dolduğunu gösteren ince çubuk
    "hook_title": True,          # giriş cümlesi ilk kareden itibaren üstte büyük başlık olarak durur
    "zoom": 0.08,                # her sahnede hafif yakınlaşma/uzaklaşma oranı (0: kapalı)
    "variety": False,            # true: her video kendine özgü bir görünüm alır (bkz. VARIETY)
}
# Çeşitlilik modu: her video bu seçeneklerden slug'ına göre sabit bir kombinasyon alır, böylece
# kanal tek bir şablon gibi görünmez. settings.json'da "variety": {"zoom": [0, 0.1]} gibi bir sözlükle
# seçenekler değiştirilebilir; listede olmayan ayarlar (ses, font, altyazı yeri) sabit kalır.
VARIETY = {
    "highlight_color": ["#4CC9F0", "#FFD60A", "#FF5D8F", "#7CFF6B", "#FF9F1C", "#B388FF"],
    "progress_bar": ["top", "top", False],   # alt kısmı Shorts arayüzü kapatıyor
    "zoom": [0, 0.05, 0.08, 0.12],
    "scene_seconds": [2.8, 3.2, 3.7],
    "sfx": [True, False],
}
POP = [(0.80, 1), (1.12, 2)]     # kelime girişi: (ölçek, kare sayısı) adımları, sonra normal boy
BAR_H = 12                       # ilerleme çubuğu kalınlığı (px)


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

def vary(cfg, slug):
    """Çeşitlilik modu açıksa bu videoya özel ayarları döndürür (aynı slug her seferinde aynı görünüm)."""
    if not cfg.get("variety"):
        return cfg
    pool = dict(VARIETY)
    if isinstance(cfg["variety"], dict):
        pool.update(cfg["variety"])
    rng = random.Random(f"variety:{slug}")
    out = dict(cfg)
    for key in sorted(pool):
        if pool[key]:
            out[key] = rng.choice(pool[key])
    return out


def look(cfg):
    """Konsolda gösterilecek kısa görünüm özeti."""
    bar = cfg["progress_bar"] or "yok"
    return (f"renk {cfg['highlight_color']}, çubuk {bar}, zoom {cfg['zoom']}, "
            f"sahne {cfg['scene_seconds']} sn, efekt {'açık' if cfg['sfx'] else 'kapalı'}")


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


def align_words(words, script, audio_len):
    """Altyazıyı senaryodaki HER kelimeye bağlar. edge-tts bazen kelime zamanlamalarının bir kısmını
    göndermiyor (yazı videonun ortasında kayboluyordu). Eşleşen kelimeler kendi zamanını kullanır,
    eksikler komşularının arasına harf sayısına göre yayılır. Dönüş: (kelimeler, eşleşme oranı)."""
    toks = script.split()
    slots, j = [], 0
    for t in toks:
        hit = next((k for k in range(j, min(j + 4, len(words))) if clean(words[k][2]) == clean(t)), None)
        if hit is None:
            slots.append([None, None, t.strip(".,!?;:")])
        else:
            slots.append(list(words[hit]))
            j = hit + 1
    matched = sum(s[0] is not None for s in slots)
    i = 0
    while i < len(slots):
        if slots[i][0] is not None:
            i += 1
            continue
        k = i
        while k < len(slots) and slots[k][0] is None:
            k += 1
        a = slots[i - 1][1] if i else 0.0
        b = slots[k][0] if k < len(slots) else max(a + 0.3 * (k - i), audio_len - 0.1)
        weights = [len(s[2]) + 2 for s in slots[i:k]]
        t, step = a, (b - a) / sum(weights)
        for s, wgt in zip(slots[i:k], weights):
            s[0], s[1] = t, t + wgt * step
            t = s[1]
        i = k
    return [tuple(s) for s in slots], matched / max(len(toks), 1)


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


SFX_WHOOSH = CACHE / "sfx_whoosh.wav"
SFX_BOOM = CACHE / "sfx_boom.wav"
_SFX_LUFS = {}


def make_sfx():
    """Whoosh ve boom efektlerini ffmpeg ile sentezler (özgün, telifsiz). Bir kez üretilip cache'e yazılır."""
    CACHE.mkdir(exist_ok=True)
    if not SFX_WHOOSH.exists():
        run(["ffmpeg", "-y", "-f", "lavfi", "-i",
             "anoisesrc=color=pink:duration=0.55:amplitude=0.9:sample_rate=44100",
             "-af", "highpass=f=450,lowpass=f=5500,afade=t=in:d=0.38:curve=exp,"
                    "afade=t=out:st=0.38:d=0.17,pan=stereo|c0=c0|c1=c0", str(SFX_WHOOSH)])
    if not SFX_BOOM.exists():
        e = "0.95*sin(2*PI*(36+80*exp(-8*t))*t)*exp(-2.6*t)"
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"aevalsrc={e}|{e}:s=44100:d=1.4",
             "-af", "lowpass=f=900,afade=t=out:st=1.0:d=0.4", str(SFX_BOOM)])
    for f in (SFX_WHOOSH, SFX_BOOM):
        if f not in _SFX_LUFS:
            _SFX_LUFS[f] = lufs(["-stream_loop", "3", "-i", str(f)])


def audio_plan(voice, music, music_start, total, cfg, durs):
    """Müzik ve efektlerin kazançlarını, efekt zamanlarını ve son normalizasyonu hesaplar."""
    v = lufs(["-i", str(voice)])
    plan = {"inputs": [], "mus": None, "sfx": None, "norm": 0.0}
    if music:
        m = lufs(["-ss", f"{music_start:.2f}", "-t", f"{total:.2f}", "-i", str(music)])
        plan["inputs"].append(["-ss", f"{music_start:.2f}", "-stream_loop", "-1", "-i", str(Path(music).resolve())])
        plan["mus"] = (v + cfg["music_rel_db"] - m, cfg["music_fade_in"])
    cuts = [sum(durs[:i + 1]) for i in range(len(durs) - 1)]
    if cfg["sfx"] and cuts:
        make_sfx()
        first = cuts[0] if cfg["first_scene_seconds"] else None   # giriş cümlesi bitince boom
        plan["inputs"] += [["-i", str(SFX_WHOOSH)], ["-i", str(SFX_BOOM)]]
        plan["sfx"] = {"cuts": cuts, "first": first,
                       "wg": v + cfg["sfx_rel_db"] - _SFX_LUFS[SFX_WHOOSH],
                       "bg": v + cfg["sfx_rel_db"] + 5 - _SFX_LUFS[SFX_BOOM]}
    extra = [a for inp in plan["inputs"] for a in inp]
    mixed = lufs(["-i", str(voice)] + extra, audio_graph(0, 1, total, plan, normalize=False))
    plan["norm"] = TARGET_LUFS - mixed
    return plan


def audio_graph(vi, xi, total, plan, normalize=True):
    """Anlatım + müzik + efektler; çıkış etiketi [a]. xi: ilk ek girişin (müzik/efekt) indeksi."""
    parts, mix, idx = [], [f"[{vi}:a]"], xi
    if plan["mus"]:
        gain, fade_in = plan["mus"]
        f = f"[{idx}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={gain:.2f}dB"
        if fade_in:
            f += f",afade=t=in:d={fade_in}"
        parts.append(f + f",afade=t=out:st={max(total - 1.2, 0):.3f}:d=1.2[bg]")
        mix.append("[bg]")
        idx += 1
    if plan["sfx"]:
        sx = plan["sfx"]
        wcuts = [c for c in sx["cuts"] if c != sx["first"]]
        if wcuts:
            parts.append(f"[{idx}:a]asplit={len(wcuts)}" + "".join(f"[ws{i}]" for i in range(len(wcuts))))
            for i, c in enumerate(wcuts):
                d = int(max(c - 0.38, 0) * 1000)   # whoosh'un en yüksek noktası tam kesmeye denk gelir
                parts.append(f"[ws{i}]adelay={d}|{d},volume={sx['wg']:.2f}dB[w{i}]")
                mix.append(f"[w{i}]")
        if sx["first"]:
            d = int(max(sx["first"] - 0.03, 0) * 1000)
            parts.append(f"[{idx + 1}:a]adelay={d}|{d},volume={sx['bg']:.2f}dB[boom]")
            mix.append("[boom]")
    # apad'e bitiş süresi verilir: sonsuz apad + atrim, ffmpeg'i son karede rastgele kilitliyordu
    pad = f"apad=whole_dur={total:.3f},atrim=0:{total:.3f}"
    if len(mix) == 1:
        g = f"[{vi}:a]{pad}"
    else:
        g = (";".join(parts) + ";" if parts else "") + "".join(mix) + \
            f"amix=inputs={len(mix)}:duration=longest:normalize=0,{pad}"
    if normalize:
        g += f",volume={plan['norm']:.2f}dB,alimiter=limit=0.89:level=false"
    return g + "[a]"


# ---------------------------------------------------------------- görsel

FONT_PATHS = [
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


@functools.lru_cache(maxsize=None)
def get_font(size):
    for fp in FONT_PATHS:
        if Path(fp).exists():
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default(size)


def clean(word):
    return re.sub(r"[^\w]", "", word).lower()


def is_highlight(word, highlights):
    return any(ch.isdigit() for ch in word) or clean(word) in highlights


def upper(word, lang="en"):
    if lang == "tr":
        word = word.replace("i", "İ").replace("ı", "I")
    return word.upper()


def make_word_png(word, path, color="white", lang="en", scale=1.0):
    """Tek kelimelik, şeffaf zeminli, renkli yazı + siyah kontur PNG. scale: giriş efekti için boy."""
    text = upper(word, lang)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    size = 140
    while size > 60:   # uzun kelimeler ekrana sığana kadar küçülür
        bbox = probe.textbbox((0, 0), text, font=get_font(size), stroke_width=10)
        if bbox[2] - bbox[0] + 40 <= 1000:
            break
        size -= 10
    size, stroke = max(int(size * scale), 20), max(round(10 * scale), 4)
    font = get_font(size)
    bbox = probe.textbbox((0, 0), text, font=font, stroke_width=stroke)
    w, h = bbox[2] - bbox[0] + 40, bbox[3] - bbox[1] + 40
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((20 - bbox[0], 20 - bbox[1]), text, font=font,
                             fill=color, stroke_width=stroke, stroke_fill="black")
    img.save(path)


def make_title_png(text, path, highlights, color, lang="en"):
    """Giriş cümlesinin tamamı: ortalanmış, en fazla 3 satır, vurgulu kelimeler renkli. İzleyici iddiayı
    ilk karede okur (kaydırıp geçme kararı yarım saniyede veriliyor)."""
    words = [upper(w, lang) for w in text.rstrip(".").split()]   # nokta yok; soru işareti kalır
    raw = text.split()
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    for size in range(104, 50, -6):
        font, stroke = get_font(size), max(size // 12, 5)
        space = probe.textlength(" ", font=font)
        lines, cur, cur_w = [], [], 0.0
        for i, w in enumerate(words):
            ww = probe.textlength(w, font=font) + 2 * stroke
            if cur and cur_w + space + ww > 960:
                lines.append(cur)
                cur, cur_w = [], 0.0
            cur_w += (space if cur else 0) + ww
            cur.append(i)
        lines.append(cur)
        if len(lines) <= 3:
            break
    asc, desc = font.getmetrics()
    lh = asc + desc + 2 * stroke
    img = Image.new("RGBA", (W, lh * len(lines) + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for li, line in enumerate(lines):
        widths = [probe.textlength(words[i], font=font) + 2 * stroke for i in line]
        x = (W - sum(widths) - space * (len(line) - 1)) / 2
        for i, wd in zip(line, widths):
            fill = color if is_highlight(raw[i], highlights) else "white"
            d.text((x + stroke, 20 + li * lh + stroke), words[i], font=font, fill=fill,
                   stroke_width=stroke, stroke_fill="black")
            x += wd + space
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

STOP = {"a", "an", "the", "of", "in", "on", "with", "and", "to", "at", "from", "for", "by", "over", "into"}


def _stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


# aramada istenmedikçe kullanılmayan klipler (ör. "paper" aramasına gelen kâğıt para, yeşil perde çekimleri)
BLOCK = ["green screen", "greenscreen", "chroma", "money", "dollar", "cash", "coin", "currency", "banknote"]


def relevant(query, text):
    """Klibin etiketleri (ya da adı) aramadaki bütün kelimeleri içeriyor mu? ("folding paper" ~ "paper, folded")
    Stok siteleri alakasız sonuçlar da döndürüyor (ör. "stack of paper" -> çizgi film bozuk para)."""
    low = text.lower().replace("-", " ")
    if any(b in low and b not in query.lower() for b in BLOCK):
        return False
    have = {_stem(t) for t in re.findall(r"[a-z]+", text.lower())}
    need = [_stem(w) for w in re.findall(r"[a-z]+", query.lower()) if w not in STOP]
    return bool(need) and all(any(h.startswith(n) or n.startswith(h) and len(h) >= 4 for h in have) for n in need)


def tag_rank(query, tags):
    """Aramanın ilk kelimesi etiket listesinde kaçıncı sırada (küçük = daha alakalı)."""
    first = next((_stem(w) for w in re.findall(r"[a-z]+", query.lower()) if w not in STOP), "")
    for i, t in enumerate(tags.lower().split(",")):
        if any(_stem(w).startswith(first) for w in re.findall(r"[a-z]+", t)):
            return i
    return 99


def fetch_pexels(query, n=4):
    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": PEXELS_KEY},
        params={"query": query, "orientation": "portrait", "per_page": 15},
        timeout=30,
    )
    r.raise_for_status()
    vids = [v for v in r.json().get("videos", []) if relevant(query, v.get("url", ""))]
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
        params={"key": PIXABAY_KEY, "q": query, "per_page": 100, "safesearch": "true"},
        timeout=30,
    )
    r.raise_for_status()
    hits = [h for h in r.json().get("hits", []) if relevant(query, h.get("tags", ""))]
    # aranan şey etiketlerin başındaysa klip asıl olarak onu gösteriyordur; en iyi adaylar arasından rastgele
    hits.sort(key=lambda h: tag_rank(query, h.get("tags", "")))
    hits = hits[:max(3 * n, 6)]
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


def brightness(clip):
    """Klibin ilk saniyelerinin ortalama parlaklığı (0-255)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(clip), "-frames:v", "1",
                          "-vf", "scale=32:32,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    return sum(raw) / len(raw) if raw else 0


def clips_for_scenes(scenes, keywords, fallback_queries):
    """Her sahneye bir klip: sahnenin kendi araması (visuals) varsa ondan, yoksa konunun keywords'ünden.
    Aynı klip mümkün olduğunca tekrar edilmez. Hiç klip yoksa [] (gradyan)."""
    need = {}
    for _, q in scenes:
        if q:
            need[q] = need.get(q, 0) + 1
    if scenes and scenes[0][1]:
        need[scenes[0][1]] += 2   # kanca sahnesi için yedek adaylar: aralarından en aydınlığı seçilir
    pools = {q: fetch_clips(q, [], c) for q, c in need.items()}
    for q, c in need.items():
        if not pools[q]:
            print(f"  '{q}' için klip bulunamadı, konunun genel aramaları kullanılacak")
    missing = sum(max(0, c - len(pools[q])) for q, c in need.items()) + sum(1 for _, q in scenes if not q)
    base = fetch_clips(keywords, fallback_queries, missing) if missing else []
    if scenes and len(pools.get(scenes[0][1], [])) > 1:
        # ilk sahne (kanca) için en aydınlık klip: kapkara bir ilk kare kaydırıp geçme sebebi
        pools[scenes[0][1]].sort(key=brightness, reverse=True)
    used, out, bi = set(), [], 0
    for _, q in scenes:
        fresh = [c for c in pools.get(q, []) if c not in used]
        if fresh:
            c = fresh[0]
        elif base:
            c, bi = base[bi % len(base)], bi + 1
        else:
            c = (pools.get(q) or [None])[0]
        used.add(c)
        out.append(c)
    avail = [c for c in out if c]
    if not avail:
        return []
    return [c or avail[i % len(avail)] for i, c in enumerate(out)]


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


def sentence_spans(script):
    """Senaryodaki cümleler: [(ilk kelime indeksi, son kelime indeksi), ...]"""
    toks = script.split()
    spans, start = [], 0
    for i, t in enumerate(toks):
        if t.rstrip("\"')").endswith((".", "!", "?")) or i == len(toks) - 1:
            spans.append((start, i))
            start = i + 1
    return spans


def plan_scenes(words, script, total, cfg, visuals):
    """Sahneler: [(süre, arama)]. topics.json'da "visuals" varsa her cümle kendi sahnesi olur ve
    görüntü o cümlenin aramasıyla bulunur (uzun cümleler ikiye bölünür). Yoksa eşit süreli sahneler."""
    if not visuals:
        return [(d, None) for d in scene_durations(total, cfg)]
    if isinstance(visuals, str):
        visuals = [visuals]
    spans = sentence_spans(script)
    if len(visuals) != len(spans):
        print(f"  uyarı: {len(spans)} cümle var ama {len(visuals)} visuals araması; eksikler sonuncuyla doldurulur")
    bounds = [0.0] + [words[a][0] for a, _ in spans[1:]] + [total]
    longest = cfg["scene_seconds"] * 1.4
    scenes = []
    for k in range(len(spans)):
        d, q = bounds[k + 1] - bounds[k], visuals[min(k, len(visuals) - 1)]
        if scenes and d < 0.7:          # çok kısa cümle ("Right?") önceki sahneye eklenir
            scenes[-1] = (scenes[-1][0] + d, scenes[-1][1])
            continue
        parts = max(1, math.ceil(d / longest))
        scenes += [(d / parts, q)] * parts
    return scenes


def render(niche, slug, total, durs, clips, words, highlights, music=None, music_start=0.0, hook=None):
    out, cfg = niche.out, niche.cfg
    use_gradient = not clips
    n = len(durs)
    palette = random.choice(cfg["palettes"])
    segs, stars, drift = [], None, 10  # drift: yıldız kayma hızı (px/sn)
    if use_gradient and cfg["starfield"]:
        stars = out / f"{slug}_stars.png"
        make_starfield(stars, W + int(max(durs) * drift) + 20, H)
    seen = {}
    for i in range(n):
        s = out / f"{slug}_seg{i}.mp4"
        seg = durs[i]
        if use_gradient:
            c0, c1, c2 = palette[i % 3], palette[(i + 1) % 3], palette[(i + 2) % 3]
            src = (f"gradients=size={W}x{H}:rate={FPS}:duration={seg:.3f}:speed=0.03:"
                   f"nb_colors=3:c0={c0}:c1={c1}:c2={c2}:seed={random.randint(1, 9999)}")
            if stars:
                cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-loop", "1", "-i", str(stars),
                       "-filter_complex", f"[0:v][1:v]overlay=x='-t*{drift}':y=0:shortest=1,{SEG_NORM}",
                       "-t", f"{seg:.3f}", "-r", str(FPS),
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(s)]
            else:
                cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-t", f"{seg:.3f}",
                       "-vf", SEG_NORM, "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(s)]
        else:
            vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            z = cfg["zoom"]
            if z:   # çift sahneler yakınlaşır, tekler uzaklaşır: durağan klipler de canlı görünür
                grow = f"t/{seg:.3f}" if i % 2 == 0 else f"(1-t/{seg:.3f})"
                vf += f"scale=w='trunc({W}*(1+{z}*{grow})/2)*2':h=-2:eval=frame,crop={W}:{H},"
            vf += f"fps={FPS},setsar=1,eq=brightness=-0.08,{SEG_NORM}"
            # aynı klip tekrar gelirse farklı bir yerinden başlar
            clip = clips[i % len(clips)]
            seen[clip] = seen.get(clip, -1) + 1
            offset = seen[clip] * 4.0
            if offset:
                offset %= max(duration(clip) - 1, 1)
            cmd = ["ffmpeg", "-y", "-ss", f"{offset:.1f}", "-stream_loop", "-1", "-i", str(clip),
                   "-t", f"{seg:.3f}",
                   "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(s)]
        run(cmd)
        segs.append(s)
    lst = out / f"{slug}_list.txt"
    lst.write_text("".join(f"file '{s.name}'\n" for s in segs))
    # altyazı: her kelime için (png, başlangıç, bitiş); pop açıksa önce küçük, sonra büyük, sonra normal boy
    pngs, shows = [], []
    for i, (s, e, w) in enumerate(words):
        if hook and s < hook[1] - 0.05:
            continue   # kanca cümlesi zaten üstte başlık olarak duruyor; ortada tekrar yazılmaz
        end = words[i + 1][0] if i + 1 < len(words) else e
        color = cfg["highlight_color"] if is_highlight(w, highlights) else "white"
        states, t0 = [], s
        if cfg["caption_pop"] and end - s > 0.15:
            for scale, frames in POP:
                states.append((scale, t0, t0 + frames / FPS))
                t0 += frames / FPS
        states.append((1.0, t0, end))
        for k, (scale, a, b) in enumerate(states):
            png = out / f"_w{i}{'abc'[k]}.png"   # kısa adlar: Windows komut satırı sınırına takılmasın
            make_word_png(w, png, color, cfg["lang"], scale)
            pngs.append(png)
            shows.append((len(pngs) + 1, a, b, "(W-w)/2:(H-h)/2"))
    title = None
    if hook:   # (metin, bitiş saniyesi): giriş cümlesi üstte başlık olarak ilk kareden itibaren
        title = out / "_title.png"
        make_title_png(hook[0], title, highlights, cfg["highlight_color"], cfg["lang"])
        pngs.append(title)
        shows.append((len(pngs) + 1, 0.0, hook[1], "(W-w)/2:H*0.17"))
    plan = audio_plan(out / f"{slug}.mp3", music, music_start, total, cfg, durs)
    # cwd=out olduğu için dosya adları göreli (yol sorunlarını önler)
    # -reinit_filter 0: sahne geçişinde filtre grafiği sıfırlanmasın (sıfırlanınca yazı PNG'leri kayboluyordu)
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-reinit_filter", "0", "-i", lst.name,
           "-i", f"{slug}.mp3"]
    for png in pngs:
        cmd += ["-i", png.name]
    for inp in plan["inputs"]:
        cmd += inp
    chain, prev = [], "0:v"
    for j, (idx, a, b, pos) in enumerate(shows):
        # yarı açık aralık: geçiş karesinde iki kelime üst üste binmesin
        chain.append(f"[{prev}][{idx}:v]overlay={pos}:enable='gte(t,{a:.3f})*lt(t,{b:.3f})'[c{j}]")
        prev = f"c{j}"
    if cfg["progress_bar"]:
        y = H - BAR_H if cfg["progress_bar"] == "bottom" else 0
        col = "0x" + cfg["highlight_color"].lstrip("#")
        chain.append(f"color=c={col}:s={W}x{BAR_H}:r={FPS}:d={total:.3f}[barc]")
        chain.append(f"[{prev}]drawbox=x=0:y={y}:w=iw:h={BAR_H}:color=black@0.45:t=fill[barbg]")
        chain.append(f"[barbg][barc]overlay=x='-w+w*t/{total:.3f}':y={y}[bar]")
        prev = "bar"
    vmap = f"[{prev}]" if chain else "0:v"
    chain.append(audio_graph(1, len(pngs) + 2, total, plan))
    cmd += ["-filter_complex", ";".join(chain)]
    cmd += ["-map", vmap, "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-t", f"{total:.3f}", f"{slug}.mp4"]
    run(cmd, cwd=out)
    for f in segs + pngs + [lst] + ([stars] if stars else []):
        f.unlink()


# ---------------------------------------------------------------- kalite kontrolü

def caption_frames(path, fps=5):
    """Ekranın ortasındaki şeritte altyazı var mı: [(saniye, True/False), ...]
    Altyazı = beyaz ya da renkli yazı + siyah kontur. Ek kütüphane gerekmez (saf Python)."""
    bw, bh = 360, 100
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                          f"fps={fps},crop={W}:300:0:{H // 2 - 150},scale={bw}:{bh}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    n, px, res = bw * bh * 3, bw * bh, []
    for k in range(len(raw) // n):
        d = raw[k * n:(k + 1) * n]
        r, g, b = d[0::3], d[1::3], d[2::3]
        txt = sum(1 for x, y, z in zip(r, g, b) if (x > 215 and y > 215 and z > 215) or (x > 200 and y > 150 and z < 110))
        blk = sum(1 for x, y, z in zip(r, g, b) if x < 40 and y < 40 and z < 40)
        res.append((k / fps, txt > 0.004 * px and blk > 0.01 * px))
    return res


def green_screen_seconds(path):
    """Ekranın çoğunu düz yeşil (yeşil perde) kaplayan saniye sayısı."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", "fps=2,scale=32:56",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    n, bad = 32 * 56 * 3, 0
    for k in range(len(raw) // n):
        d = raw[k * n:(k + 1) * n]
        g = sum(1 for r, gg, b in zip(d[0::3], d[1::3], d[2::3]) if gg > 140 and gg > r + 50 and gg > b + 50)
        bad += g > 0.5 * 32 * 56
    return bad / 2


def check_video(path, expected=None, speech=None):
    """Videoyu kontrol eder, bulunan sorunların listesini döndürür (boş liste = sorun yok)."""
    try:
        d = duration(path)
    except Exception:
        return ["dosya okunamıyor"]
    problems = []
    if expected and abs(d - expected) > 0.5:
        problems.append(f"süre {d:.1f} sn, beklenen {expected:.1f} sn")
    if d >= 60:
        problems.append(f"{d:.0f} sn: Shorts için 60 saniyenin altında olmalı")
    loud = lufs(["-i", str(path)])
    if not -16 <= loud <= -12:
        problems.append(f"ses seviyesi {loud:.1f} LUFS (hedef {TARGET_LUFS})")
    green = green_screen_seconds(path)
    if green >= 0.5:
        problems.append(f"{green:.1f} sn yeşil perde görüntüsü")
    a, b = speech or (0.4, d - 0.8)
    hits = [ok for t, ok in caption_frames(path) if a <= t <= b]
    if hits:
        cover = sum(hits) / len(hits)
        gap = longest = 0
        for ok in hits:
            gap = 0 if ok else gap + 1
            longest = max(longest, gap)
        if cover < 0.85 or longest > 5:
            problems.append(f"altyazı eksik (konuşmanın %{cover * 100:.0f}'inde var, en uzun boşluk {longest / 5:.1f} sn)")
    return problems


# ---------------------------------------------------------------- ana akış

async def produce(niche):
    base = cfg = niche.cfg
    migrate_legacy_output(niche)
    niche.out.mkdir(parents=True, exist_ok=True)
    n_music = len(niche.music_tracks()) if cfg["music"] else 0
    print(f"\n=== {cfg.get('channel', niche.name)} ({niche.name}) — {len(niche.topics)} konu, "
          f"ses: {cfg['voice']}, müzik: {n_music} parça ===")
    made, failed = 0, []
    for t in niche.topics:
        slug = t["slug"]
        if (niche.out / f"{slug}.mp4").exists():
            print(f"atlandı (zaten var): {slug}")
            continue
        print(f"üretiliyor: {slug}")
        niche.cfg = cfg = vary(base, slug)   # render() ayarları niche.cfg'den okur
        if base.get("variety"):
            print(f"  görünüm: {look(cfg)}")
        mp3 = niche.out / f"{slug}.mp3"
        voice = cfg["voice"]
        for attempt in range(3):
            try:
                words = await tts(t["script"], mp3, voice, cfg["rate"])
            except Exception as e:
                if voice == DEFAULTS["voice"]:
                    raise
                print(f"  uyarı: '{voice}' sesi çalışmadı ({type(e).__name__}), '{DEFAULTS['voice']}' kullanılıyor")
                voice = DEFAULTS["voice"]
                words = await tts(t["script"], mp3, voice, cfg["rate"])
            words, ratio = align_words(words, t["script"], duration(mp3))
            if ratio >= 0.95:
                break
            print(f"  uyarı: kelime zamanlamalarının %{100 - ratio * 100:.0f}'i eksik geldi, ses yeniden üretiliyor")
        if ratio == 0:
            sys.exit("Ses üretilemedi (kelime zamanlaması gelmedi). İnternet bağlantını kontrol et.")
        if ratio < 0.95:
            print("  uyarı: eksik kelimelerin zamanı tahmin edildi")
        total = duration(mp3) + 0.3
        scenes = plan_scenes(words, t["script"], total, cfg, t.get("visuals"))
        hook = None
        if cfg["hook_title"]:
            spans = sentence_spans(t["script"])
            a, b = spans[0]
            end = words[spans[1][0]][0] if len(spans) > 1 else words[b][1]
            hook = (" ".join(t["script"].split()[a:b + 1]), end)
        highlights = {clean(h) for h in t.get("highlight", [])}
        music, music_start = pick_music(niche, t)
        if music:
            print(f"  müzik: {music.name} ({music_start:.0f}. saniyeden)")
        video = niche.out / f"{slug}.mp4"
        for attempt in range(2):
            clips = clips_for_scenes(scenes, t["keywords"], cfg["fallback_queries"])
            if not clips and attempt == 0:
                print("  stok klip yok, gradyan arka plan kullanılıyor")
            render(niche, slug, total, [d for d, _ in scenes], clips, words, highlights, music, music_start, hook)
            start = hook[1] if hook else words[0][0]   # kanca sırasında ortada yazı yok
            problems = check_video(video, total, (start + 0.1, words[-1][1] - 0.1))
            if not problems:
                break
            print(f"  KALİTE KONTROLÜ: {'; '.join(problems)}")
            if attempt == 0:
                print("  farklı kliplerle yeniden üretiliyor...")
        if problems:
            bad = niche.out / f"{slug}.HATALI.mp4"
            bad.unlink(missing_ok=True)
            video.rename(bad)
            print(f"  ! kontrolden geçmedi, {bad.name} olarak bırakıldı (yükleme). Sonraki çalıştırmada yeniden denenir.")
            failed.append(f"{niche.name}/{slug}")
            continue
        (niche.out / f"{slug}.txt").write_text(
            f"{t['title']}\n\n{t.get('description', '')}\n\n{t.get('tags', cfg['default_tags'])}",
            encoding="utf-8",
        )
        made += 1
        print(f"  hazır: output/{niche.name}/{slug}.mp4 ({total:.1f} sn, {len(scenes)} sahne, kontrol: tamam)")
    niche.cfg = base
    return made, failed


def check_existing(niches):
    """--check: hazır videoları kalite kontrolünden geçirir."""
    bad = 0
    for n in niches:
        for t in n.topics:
            p = n.out / f"{t['slug']}.mp4"
            if not p.exists():
                continue
            problems = check_video(p, speech=(2.2, duration(p) - 0.8))   # ilk ~2 sn kanca başlığı
            bad += bool(problems)
            print(f"{'SORUNLU' if problems else 'tamam  '}  {n.name}/{t['slug']}" +
                  (f"  ->  {'; '.join(problems)}" if problems else ""))
    print(f"\n{bad} sorunlu video." if bad else "\nHepsi kontrolden geçti.")


async def main():
    args = sys.argv[1:]
    if "--list" in args:
        for n in find_niches([]):
            done = sum((n.out / f"{t['slug']}.mp4").exists() for t in n.topics)
            print(f"{n.name:15s} {done}/{len(n.topics)} video hazır   ({n.cfg.get('channel', '')})")
        return
    if "--check" in args:
        check_existing(find_niches([a for a in args if a != "--check"]))
        return
    niches = find_niches(args)
    mode = "Pixabay" if PIXABAY_KEY else ("Pexels" if PEXELS_KEY else "gradyan (key'siz)")
    print(f"Görsel kaynağı: {mode} | Nişler: {', '.join(n.name for n in niches)}")
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg bulunamadı, önce kur.")
    CACHE.mkdir(exist_ok=True)
    total, failed = 0, []
    for n in niches:
        made, bad = await produce(n)
        total += made
        failed += bad
    print(f"\nToplam {total} yeni video üretildi.")
    if failed:
        print(f"Kontrolden geçemeyen {len(failed)} video (.HATALI.mp4): {', '.join(failed)}")


if __name__ == "__main__":
    asyncio.run(main())
