"""
Kodla çizilen bilim sahneleri: stok video yerine gerçek ölçülerle hesaplanmış animasyonlar.

topics.json'da bir konunun "sim" alanı sahnenin kurgusunu tarif eder, "visuals" listesinde "sim:<adım>"
yazan cümleler bu motorla çizilir (diğerleri yine stok klip olabilir). Her kare numpy + Pillow ile çizilir
ve doğrudan FFmpeg'e verilir; ek program gerekmez. Bütün gök cisimleri telifsiz, tamamen prosedüreldir.

Şablonlar:
  lineup   "Hepsi bu boşluğa sığar": iki cisim arasına (ör. Dünya-Ay) başka cisimler gerçek boyutlarıyla dizilir.
           Adımlar: intro, stack, measure, overflow, stretch, spare
  compare  "Büyüğün içine kaç tane küçük sığar": iki cisim gerçek ölçekte yan yana, küçükler büyüğün içini doldurur,
           sonra çapı boyunca dizilir. Adımlar: intro, point, spot, back, fill, across, outro
"""
import functools
import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
TW, TH = 2048, 1024            # doku çözünürlüğü (eşdikdörtgen harita)
LIGHT = np.array([-0.45, 0.35, 0.82])
LIGHT = LIGHT / np.linalg.norm(LIGHT)
YELLOW = (255, 214, 10)
RED = (255, 84, 72)
GREEN = (88, 232, 128)

# Gerçek ölçüler (km). Çaplar NASA gezegen bilgi sayfasındaki ekvator çapları; Ay mesafeleri NASA Moon Fact Sheet.
BODIES = {
    "sun":     {"name": "Sun",     "d": 1391400, "star": True, "atmo": (1.0, 0.62, 0.18)},   # IAU nominal yarıçap 695.700 km
    "mercury": {"name": "Mercury", "d": 4879},
    "venus":   {"name": "Venus",   "d": 12104, "atmo": (1.0, 0.9, 0.7)},
    "earth":   {"name": "Earth",   "d": 12756, "atmo": (0.45, 0.7, 1.0)},
    "moon":    {"name": "Moon",    "d": 3475},
    "mars":    {"name": "Mars",    "d": 6792,  "atmo": (1.0, 0.6, 0.45)},
    "jupiter": {"name": "Jupiter", "d": 142984, "gas": True},
    "saturn":  {"name": "Saturn",  "d": 120536, "gas": True, "rings": True},
    "uranus":  {"name": "Uranus",  "d": 51118, "gas": True, "atmo": (0.6, 0.95, 1.0)},
    "neptune": {"name": "Neptune", "d": 49528, "gas": True, "atmo": (0.4, 0.6, 1.0)},
}
TEMPLATES = {"lineup": ("intro", "stack", "measure", "overflow", "stretch", "spare"),
             "compare": ("intro", "point", "spot", "back", "fill", "across", "outro")}

FONT_PATHS = [
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


@functools.lru_cache(maxsize=None)
def font(size):
    for fp in FONT_PATHS:
        if Path(fp).exists():
            return ImageFont.truetype(fp, size)
    return ImageFont.load_default(size)


# ---------------------------------------------------------------- yardımcılar

def ease(u):
    """Yumuşak başlayıp yumuşak biten geçiş (0-1)."""
    u = min(max(u, 0.0), 1.0)
    return u * u * (3 - 2 * u)


def ease_out_back(u):
    """Hedefi hafifçe geçip yerine oturan giriş (gezegenler 'pat' diye yerleşir)."""
    u = min(max(u, 0.0), 1.0)
    c = 1.9
    return 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2


def span(u, a, b):
    """u'nun [a, b] aralığındaki ilerlemesi (0-1)."""
    return min(max((u - a) / (b - a), 0.0), 1.0)


def lerp(a, b, t):
    return a + (b - a) * t


# ---------------------------------------------------------------- prosedürel dokular

def _up(grid, h, w):
    """Küçük rastgele ızgarayı yumuşakça büyütür; yatayda sarar (doku dönünce dikiş görünmez)."""
    gh, gw = grid.shape
    ys = np.linspace(0, gh - 1, h)
    xs = np.arange(w) * gw / w
    y0 = np.floor(ys).astype(int)
    y1 = np.minimum(y0 + 1, gh - 1)
    x0 = np.floor(xs).astype(int) % gw
    x1 = (x0 + 1) % gw
    fy, fx = ys - np.floor(ys), xs - np.floor(xs)
    fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)
    top = grid[y0][:, x0] * (1 - fx) + grid[y0][:, x1] * fx
    bot = grid[y1][:, x0] * (1 - fx) + grid[y1][:, x1] * fx
    return top * (1 - fy)[:, None] + bot * fy[:, None]


def fbm(seed, base=4, octaves=6, h=TH, w=TW):
    """Çok katmanlı gürültü (0-1): kıtalar, bulutlar, kraterli yüzeyler için."""
    rng = np.random.default_rng(seed)
    out, amp, tot = np.zeros((h, w), np.float32), 1.0, 0.0
    for o in range(octaves):
        gw = base * 2 ** o
        gh = max(2, round(gw * h / w) + 1)   # hücreler kare kalsın (uzun ekranda dikey çizgi olmasın)
        out += amp * _up(rng.random((gh, gw)).astype(np.float32), h, w)
        tot += amp
        amp *= 0.5
    return out / tot


def _mix(a, b, t):
    t = np.clip(t, 0, 1)[..., None]
    return np.asarray(a, np.float32) * (1 - t) + np.asarray(b, np.float32) * t


def _lat():
    return ((0.5 - (np.arange(TH) + 0.5) / TH) * math.pi)[:, None] * np.ones((1, TW), np.float32)


def _lon():
    return np.ones((TH, 1), np.float32) * ((np.arange(TW) + 0.5) / TW * 2 * math.pi - math.pi)[None, :]


def _craters(img, seed, count, rmin, rmax, depth=0.10):
    rng = np.random.default_rng(seed)
    for _ in range(count):
        lat0 = rng.uniform(-1.3, 1.3)
        lon0 = rng.uniform(-math.pi, math.pi)
        rad = rng.uniform(rmin, rmax) * (1 if rng.random() < 0.85 else 2.5)
        rows = int(rad / math.pi * TH) + 2
        cols = int(rad / math.cos(lat0) / (2 * math.pi) * TW) + 2
        r0 = int((0.5 - lat0 / math.pi) * TH)
        c0 = int((lon0 + math.pi) / (2 * math.pi) * TW)
        rr = np.arange(r0 - rows, r0 + rows + 1)
        cc = np.arange(c0 - cols, c0 + cols + 1)
        rr = rr[(rr >= 0) & (rr < TH)]
        lat = (0.5 - (rr + 0.5) / TH) * math.pi
        lon = (cc + 0.5) / TW * 2 * math.pi - math.pi
        d = np.sqrt(((lon[None, :] - lon0) * math.cos(lat0)) ** 2 + (lat[:, None] - lat0) ** 2) / rad
        delta = np.where(d < 0.85, -depth, 0) + np.exp(-((d - 0.95) / 0.09) ** 2) * depth * 1.2
        img[np.ix_(rr, cc % TW)] += delta[..., None].astype(np.float32)


@functools.lru_cache(maxsize=None)
def texture(body):
    """Gök cisminin yüzey haritası (TH x TW x 3, 0-1). Tamamen kodla üretilir, telifsiz."""
    lat = _lat()
    if body == "sun":
        n = fbm(61, base=32, octaves=4)
        m = fbm(67, base=6, octaves=4)
        img = _mix((1.0, 0.50, 0.08), (1.0, 0.86, 0.42), n * 1.6 - 0.35 + (m - 0.5) * 0.6)
    elif body == "earth":
        n = fbm(3, base=6, octaves=7)
        land = n > np.quantile(n, 0.70)
        ocean = _mix((0.04, 0.17, 0.44), (0.09, 0.40, 0.70), (n - 0.3) / 0.4)
        g = fbm(5, base=8, octaves=5)
        ground = _mix((0.16, 0.36, 0.12), (0.62, 0.50, 0.30), (g - 0.35) / 0.35 + 0.3 * np.exp(-(lat / 0.4) ** 2))
        img = np.where(land[..., None], ground, ocean)
        img = _mix(img, (0.93, 0.95, 0.98), (np.abs(lat) - 1.22 - 0.25 * (g - 0.5)) * 12)
        c = fbm(7, base=6, octaves=6)
        img = _mix(img, (0.97, 0.98, 1.0), (c - 0.47) * 2.8)
    elif body in ("moon", "mercury"):
        n = fbm(11 if body == "moon" else 13, base=6, octaves=7)
        tint = (0.66, 0.66, 0.68) if body == "moon" else (0.62, 0.58, 0.54)
        img = np.asarray(tint, np.float32) * (0.82 + 0.4 * (n - 0.5))[..., None]
        if body == "moon":
            m = fbm(17, base=3, octaves=4)
            img = _mix(img, (0.33, 0.33, 0.36), (m - np.quantile(m, 0.70)) * 9)
        _craters(img, 23 if body == "moon" else 29, 260, 0.012, 0.05)
    elif body == "venus":
        n = fbm(31, base=5, octaves=6)
        s = np.sin(lat * 7 + 4 * n)
        img = _mix((0.96, 0.88, 0.66), (0.80, 0.64, 0.40), s * 0.5 + 0.5)
    elif body == "mars":
        n = fbm(37, base=5, octaves=7)
        img = _mix((0.80, 0.42, 0.22), (0.44, 0.22, 0.12), (n - np.quantile(n, 0.62)) * 7)
        img = np.where((np.abs(lat) > 1.30)[..., None], np.float32(0.95), img)
    elif body in ("jupiter", "saturn"):
        n = fbm(41 if body == "jupiter" else 43, base=8, octaves=6)
        la = lat + 0.05 * (n - 0.5) * (1 if body == "jupiter" else 0.4)
        if body == "jupiter":
            s = np.sin(la * 9) + 0.45 * np.sin(la * 23 + 1.3)
            img = _mix((0.95, 0.90, 0.80), (0.68, 0.45, 0.30), s * 0.45 + 0.4)
            img = _mix(img, (0.85, 0.62, 0.42), np.abs(np.sin(la * 4.5)) * 0.35)
            lon = _lon()
            spot = (((lon - 0.4) / 0.22) ** 2 + ((lat + 0.39) / 0.075) ** 2)
            img = _mix(img, (0.80, 0.36, 0.22), np.exp(-spot ** 2) * 1.2)
        else:
            s = np.sin(la * 8) + 0.3 * np.sin(la * 19)
            img = _mix((0.95, 0.86, 0.64), (0.78, 0.65, 0.44), s * 0.3 + 0.45)
        img = _mix(img, (0.45, 0.40, 0.38), (np.abs(lat) - 1.15) * 2)
    elif body == "uranus":
        n = fbm(47, base=4, octaves=4)
        img = _mix((0.72, 0.91, 0.93), (0.60, 0.84, 0.88), np.sin(lat * 5) * 0.3 + 0.4 + (n - 0.5) * 0.2)
    elif body == "neptune":
        n = fbm(53, base=6, octaves=6)
        la = lat + 0.04 * (n - 0.5)
        img = _mix((0.30, 0.48, 0.92), (0.16, 0.28, 0.72), np.sin(la * 10) * 0.4 + 0.5)
        lon = _lon()
        spot = (((lon + 0.6) / 0.18) ** 2 + ((lat + 0.35) / 0.07) ** 2)
        img = _mix(img, (0.08, 0.12, 0.40), np.exp(-spot ** 2))
    else:
        raise ValueError(f"bilinmeyen gök cismi: {body}")
    return np.clip(img, 0, 1).astype(np.float32)


# ---------------------------------------------------------------- çizim

def sphere_patch(body, cx, cy, r, rot=0.0):
    """Ekranın görünen kısmına düşen küre (+ atmosfer ışıması): (RGBA Image, x0, y0) ya da None.
    Sadece görünen pikseller hesaplanır: dev yakınlaştırmalarda da hızlı kalır."""
    info = BODIES[body]
    star = info.get("star")
    halo = 0.22 if star else 0.07 if info.get("atmo") else 0.0
    ext = r * (1 + halo) + 2
    x0, x1 = max(0, int(cx - ext)), min(W, int(cx + ext) + 1)
    y0, y1 = max(0, int(cy - ext)), min(H, int(cy + ext) + 1)
    if r < 0.6 or x0 >= x1 or y0 >= y1:
        return None
    x = (np.arange(x0, x1, dtype=np.float32) + 0.5 - cx) / r
    y = (np.arange(y0, y1, dtype=np.float32) + 0.5 - cy) / r
    x, y = np.meshgrid(x, y)
    r2 = x * x + y * y
    rr = np.sqrt(r2)
    z = np.sqrt(np.clip(1 - r2, 0, 1))
    lat = np.arcsin(np.clip(-y, -1, 1))
    lon = np.arctan2(x, np.maximum(z, 1e-4)) + rot
    tex = texture(body)
    u = (((lon / (2 * math.pi) + 0.5) % 1) * TW).astype(np.int32) % TW
    v = np.clip(((0.5 - lat / math.pi) * TH).astype(np.int32), 0, TH - 1)
    col = tex[v, u]
    diffuse = np.clip(x * LIGHT[0] - y * LIGHT[1] + z * LIGHT[2], 0, 1)
    shade = 0.035 + 0.965 * diffuse ** 0.85
    if star:   # yıldız kendi ışığını verir: gölge yok, kenara doğru hafif kararır
        diffuse, shade = np.ones_like(z), 0.55 + 0.45 * z ** 0.5
    if info.get("gas"):
        shade *= 0.6 + 0.4 * z
    col = col * shade[..., None]
    if info.get("atmo") and not star:
        rim = (1 - z) ** 3 * np.clip(diffuse + 0.25, 0, 1) * 0.55
        col = col + rim[..., None] * np.asarray(info["atmo"], np.float32)
    alpha = np.clip((1 - rr) * r + 0.5, 0, 1)
    if halo:
        lit = 1.0 if star else np.clip((x * LIGHT[0] - y * LIGHT[1]) / np.maximum(rr, 1e-4) * 0.6 + 0.55, 0.15, 1)
        h = np.where(rr > 1, np.exp(-(rr - 1) / (halo * 0.35)) * (0.85 if star else 0.55) * lit, 0)
        glow = np.asarray(info["atmo"], np.float32)
        col = np.where((rr > 1)[..., None], glow, col)
        alpha = np.maximum(alpha, h * (rr > 1))
    rgba = np.dstack([np.clip(col, 0, 1) * 255, alpha * 255]).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA"), x0, y0


RING_BANDS = [   # Satürn halkaları, gezegen yarıçapına oranla (iç, dış, opaklık)
    (1.239, 1.527, 0.22),   # C halkası
    (1.527, 1.951, 0.88),   # B halkası
    (2.027, 2.269, 0.66),   # A halkası (arada Cassini boşluğu)
]
RING_TILT = 0.22


def ring_patch(cx, cy, r, front):
    """Satürn halkasının arka (gezegenin arkasında kalan) ya da ön yarısı."""
    ext_x, ext_y = r * 2.3, r * 2.3 * RING_TILT + 2
    x0, x1 = max(0, int(cx - ext_x)), min(W, int(cx + ext_x) + 1)
    y0, y1 = (max(0, int(cy)), min(H, int(cy + ext_y) + 1)) if front else \
        (max(0, int(cy - ext_y)), min(H, int(cy) + 1))
    if r < 2 or x0 >= x1 or y0 >= y1:
        return None
    x = (np.arange(x0, x1, dtype=np.float32) + 0.5 - cx) / r
    y = (np.arange(y0, y1, dtype=np.float32) + 0.5 - cy) / r
    x, y = np.meshgrid(x, y)
    rr = np.sqrt(x * x + (y / RING_TILT) ** 2)
    a = np.zeros_like(rr)
    for lo, hi, op in RING_BANDS:
        edge = np.clip((rr - lo) * r * 0.5, 0, 1) * np.clip((hi - rr) * r * 0.5, 0, 1)
        a += edge * op
    a *= 0.85 + 0.15 * np.sin(rr * 90)
    shade = 0.87 - 0.13 * np.clip(x / 2.3, -1, 1)             # ışık soldan
    col = np.dstack([0.88 * shade, 0.80 * shade, 0.64 * shade]) * 255
    if not front:   # arka yarı: gezegen diskinin içinde kalan kısım görünmez (ön yarı gezegenin üstüne çizilir)
        a = np.where(x * x + y * y < 1, 0, a)
    rgba = np.dstack([col, np.clip(a, 0, 1) * 255]).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA"), x0, y0


@functools.lru_cache(maxsize=4)
def background(seed=1):
    """Yıldızlı uzay: binlerce küçük yıldız, birkaç parlak yıldız ve çok hafif bir bulutsu."""
    rng = np.random.default_rng(seed)
    neb = fbm(seed + 100, base=3, octaves=5, h=H // 4, w=W // 4)
    neb2 = fbm(seed + 200, base=2, octaves=4, h=H // 4, w=W // 4)
    img = np.zeros((H // 4, W // 4, 3), np.float32)
    img += np.clip(neb - 0.45, 0, 1)[..., None] * np.array([0.30, 0.12, 0.45]) * 0.55
    img += np.clip(neb2 - 0.5, 0, 1)[..., None] * np.array([0.05, 0.20, 0.45]) * 0.55
    img += np.array([0.012, 0.012, 0.03])
    base = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
    d = ImageDraw.Draw(base)
    for _ in range(1400):
        x, y = rng.integers(0, W), rng.integers(0, H)
        b = int(rng.integers(70, 230))
        t = [(255, 255, 255), (200, 220, 255), (255, 236, 215)][rng.integers(0, 3)]
        c = tuple(int(v * b / 255) for v in t)
        if rng.random() < 0.85:
            d.point((x, y), fill=c)
        else:
            d.ellipse((x - 1, y - 1, x + 1, y + 1), fill=c)
    for _ in range(18):
        x, y = rng.integers(0, W), rng.integers(0, H)
        for rad, a in ((7, 40), (4, 90), (2, 255)):
            d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=(a, a, a))
    return base.convert("RGBA")


def text(layer, xy, s, size, fill=(255, 255, 255), anchor="mm", stroke=4):
    ImageDraw.Draw(layer).text(xy, s, font=font(size), fill=fill, anchor=anchor,
                               stroke_width=stroke, stroke_fill=(0, 0, 0))


def pill(layer, xy, s, size, color, anchor="mm", alpha=1.0):
    """Yarı saydam koyu zeminli renkli etiket (ölçü sayıları için)."""
    d = ImageDraw.Draw(layer)
    l, t, r, b = d.textbbox(xy, s, font=font(size), anchor=anchor)
    a = int(190 * alpha)
    d.rounded_rectangle((l - 18, t - 12, r + 18, b + 12), radius=14, fill=(5, 6, 20, a),
                        outline=(*color, int(255 * alpha)), width=3)
    d.text(xy, s, font=font(size), fill=(*color, int(255 * alpha)), anchor=anchor)


def ruler(layer, x, ya, yb, color, alpha=1.0, cap=26):
    """Dikey ölçü çizgisi; uçlarında yatay çentik."""
    if alpha <= 0:
        return
    d = ImageDraw.Draw(layer)
    c = (*color, int(255 * alpha))
    ya, yb = sorted((ya, yb))
    d.line((x, max(ya, -10), x, min(yb, H + 10)), fill=c, width=6)
    for y in (ya, yb):
        if -10 < y < H + 10:
            d.line((x - cap, y, x + cap, y), fill=c, width=6)


# ---------------------------------------------------------------- şablon: lineup

class Lineup:
    """Alt cisim (ör. Dünya) ile üst cisim (ör. Ay) arasına 'stack' listesindeki cisimler gerçek çaplarıyla
    dizilir. Dünya merkezi = 0 km, yukarı doğru artar. Ekran dikey: Dünya altta, Ay üstte."""

    def __init__(self, spec):
        self.bottom, self.top = spec.get("bottom", "earth"), spec.get("top", "moon")
        self.stack = list(spec["stack"])
        self.near, self.far = float(spec["near_km"]), float(spec["far_km"])
        rb, rt = BODIES[self.bottom]["d"] / 2, BODIES[self.top]["d"] / 2
        self.rb, self.rt = rb, rt
        y, self.centers = rb, []
        for b in self.stack:
            dia = BODIES[b]["d"]
            self.centers.append(y + dia / 2)
            y += dia
        self.stack_top = y
        self.stack_len = y - rb
        self.gap_near = self.near - rb - rt
        self.gap_far = self.far - rb - rt
        # kameralar: (ölçek px/km, ekranın ortasına gelen km)
        s_full = (H - 200) / (self.far + rt + rb)
        self.full = (s_full, (H / 2 - 90) / s_full - rb)
        self.close = (620 / rb, (1330 - H / 2) / (620 / rb))   # kanca karesi: büyük, parlak Dünya
        s_top = s_full * 4
        self.top_cam = (s_top, self.far - (H / 2 - 500) / s_top)
        spare_mid = (self.stack_top + self.far - rt) / 2
        s_sp = 420 / max(self.gap_far - self.stack_len, 1)
        self.spare_cam = (s_sp, spare_mid - (H / 2 - 600) / s_sp)

    # kamera geçişi: ölçek logaritmik, konum ekran üzerinde düzgün kayar
    @staticmethod
    def cam_mix(a, b, t, anchor):
        s = math.exp(lerp(math.log(a[0]), math.log(b[0]), t))
        ya = H / 2 - (anchor - a[1]) * a[0]
        yb = H / 2 - (anchor - b[1]) * b[0]
        return s, anchor - (H / 2 - lerp(ya, yb, t)) / s

    def cue(self, words, name, default):
        """Cismin adının söylendiği an (sahne içi 0-1); bulunamazsa default."""
        for u, w in words or []:
            if w.lower().strip(".,!?;:") == name.lower():
                return max(0.0, u - 0.04)
        return default

    def state(self, step, u, words=None):
        st = {"cam": self.full, "moon": self.near, "placed": len(self.stack), "pop": [1.0] * len(self.stack),
              "stack_ruler": 0.0, "gap_ruler": 0.0, "gap_label": None, "gap_glow": 0.0, "red": 0.0,
              "spare": 0.0, "rot": 0.0, "labels": 1.0}
        if step == "intro":
            st.update(cam=self.cam_mix(self.close, self.full, ease(span(u, 0.0, 0.72)), 0.0),
                      placed=0, pop=[0.0] * len(self.stack), rot=u * 0.5, gap_glow=span(u, 0.55, 0.9),
                      labels=span(u, 0.55, 0.85))
        elif step == "stack":
            n = len(self.stack)
            starts = [self.cue(words, BODIES[b]["name"], 0.10 + 0.78 * k / n) for k, b in enumerate(self.stack)]
            pops = [ease_out_back(span(u, a, a + 0.16)) for a in starts]
            st.update(pop=pops, gap_glow=1 - span(u, 0.0, 0.2))
        elif step == "measure":
            st.update(stack_ruler=ease(span(u, 0.05, 0.8)))
        elif step == "overflow":
            t = ease(span(u, 0.0, 0.55))
            st.update(cam=self.cam_mix(self.full, self.top_cam, t, self.far), stack_ruler=1.0,
                      gap_ruler=span(u, 0.15, 0.5), red=span(u, 0.2, 0.45), labels=1 - t)
        elif step == "stretch":
            t = ease(span(u, 0.12, 0.85))
            st.update(cam=self.top_cam, moon=lerp(self.near, self.far, t), stack_ruler=1.0, gap_ruler=1.0,
                      red=1 - span(u, 0.0, 0.6) if self.near - self.rt < self.stack_top else 0.0, labels=0.0)
        elif step == "spare":
            t = ease(span(u, 0.0, 0.6))
            st.update(cam=self.cam_mix(self.top_cam, self.spare_cam, t, self.far), moon=self.far,
                      stack_ruler=1 - t, gap_ruler=1 - t, spare=span(u, 0.35, 0.7), labels=0.0)
        else:
            raise ValueError(f"lineup şablonunda '{step}' adımı yok: {', '.join(TEMPLATES['lineup'])}")
        return st

    def frame(self, step, u, words=None):
        st = self.state(step, u, words)
        s, cy = st["cam"]
        sy = lambda km: H / 2 - (km - cy) * s
        img = background().copy()
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))

        def put(patch):
            if patch:
                img.alpha_composite(patch[0], (patch[1], patch[2]))

        # alt cisim (Dünya)
        put(sphere_patch(self.bottom, W / 2, sy(0), self.rb * s, st["rot"]))
        # dizilen cisimler
        for k, b in enumerate(self.stack):
            p = st["pop"][k]
            if p <= 0.001:
                continue
            r = BODIES[b]["d"] / 2 * s * p
            cyk = sy(self.centers[k])
            if BODIES[b].get("rings"):
                put(ring_patch(W / 2, cyk, r, front=False))
            put(sphere_patch(b, W / 2, cyk, r, 0.3 * k))
            if BODIES[b].get("rings"):
                put(ring_patch(W / 2, cyk, r, front=True))
        # üst cisim (Ay) en önde: taşma varsa üstte görünür
        put(sphere_patch(self.top, W / 2, sy(st["moon"]), self.rt * s, 0.0))

        gap_top = sy(st["moon"] - self.rt)
        gap_bot = sy(self.rb)
        d = ImageDraw.Draw(layer)
        # "bu boşluk": Dünya ile Ay arası kesik çizgi
        if st["gap_glow"] > 0:
            a = int(230 * st["gap_glow"])
            y = gap_bot - 8
            while y > gap_top + 8:
                d.line((W / 2, y, W / 2, max(y - 22, gap_top + 8)), fill=(*YELLOW, a), width=5)
                y -= 40
            for yy in (gap_top, gap_bot):
                d.line((W / 2 - 60, yy, W / 2 + 60, yy), fill=(*YELLOW, a), width=5)
        # taşma: Ay'ın altı ile yığının tepesi arası kırmızı
        if st["red"] > 0 and st["moon"] - self.rt < self.stack_top:
            pulse = 0.55 + 0.45 * math.sin(u * 14)
            a = int(110 * st["red"] * pulse)
            d.rectangle((W / 2 - 330, sy(self.stack_top), W / 2 + 330, gap_top), fill=(*RED, a))
        # isim etiketleri
        if st["labels"] > 0.01:
            self.labels(layer, st, sy, s)
        # yığın cetveli (sağda, sarı)
        if st["stack_ruler"] > 0:
            g = st["stack_ruler"]
            top = sy(self.rb + self.stack_len * g)
            alpha = min(1.0, g * 4) if step != "spare" else g
            ruler(layer, W - 70, gap_bot, top, YELLOW, alpha)
            km = round(self.stack_len * (ease(min(g / 0.97, 1)) if step == "measure" else 1))
            y = max(top + 70, 260)
            if 840 < y < 1080:
                y = 840 if y < 960 else 1080
            pill(layer, (W - 110, y), f"{km:,} km", 44, YELLOW, anchor="rm", alpha=alpha)
        # boşluk cetveli (solda; sığmıyorsa kırmızı, sığıyorsa yeşil)
        if st["gap_ruler"] > 0:
            gap = st["moon"] - self.rt - self.rb
            col = GREEN if gap >= self.stack_len else RED
            ruler(layer, 70, gap_bot, gap_top, col, st["gap_ruler"])
            y = gap_top - 60 if gap_top > 300 else gap_top + 70
            if 840 < y < 1080:   # ortadaki altyazı şeridine binmesin
                y = 840 if y < 960 else 1080
            pill(layer, (110, y), f"{round(gap):,} km", 44, col, anchor="lm", alpha=st["gap_ruler"])
        # artan boşluk
        if st["spare"] > 0:
            a = st["spare"]
            top_y, bot_y = gap_top, sy(self.stack_top)
            ruler(layer, W / 2 + 230, top_y, bot_y, GREEN, a, cap=40)
            left = round(self.gap_far - self.stack_len)
            pill(layer, (W / 2 + 200, (top_y + bot_y) / 2), f"{left:,} km", 52, GREEN, anchor="rm", alpha=a)
            text(layer, (W / 2 + 200, (top_y + bot_y) / 2 + 70), "TO SPARE", 40,
                 fill=(*GREEN, int(255 * a)), anchor="rm")
        img.alpha_composite(layer)
        return img.convert("RGB")

    def labels(self, layer, st, sy, s):
        a = st["labels"]
        items = [(self.bottom, sy(0), self.rb * s, 1.0), (self.top, sy(st["moon"]), self.rt * s, 1.0)]
        items += [(b, sy(self.centers[k]), BODIES[b]["d"] / 2 * s * st["pop"][k], st["pop"][k])
                  for k, b in enumerate(self.stack)]
        side = 1
        for b, y, r, p in items:
            if p < 0.3 or not -50 < y < H + 50:
                continue
            fill = (255, 255, 255, int(255 * a * min(1, p)))
            name = BODIES[b]["name"].upper()
            if r > 75:   # büyük gezegen: adı üstünde
                text(layer, (W / 2, y), name, 40, fill=fill)
                continue
            side = -side
            d = ImageDraw.Draw(layer)
            x_edge = W / 2 + side * (r + 8)
            x_txt = W / 2 + side * (r + 70)
            d.line((x_edge, y, x_txt - side * 10, y), fill=(255, 255, 255, int(160 * a)), width=3)
            text(layer, (x_txt, y), name, 34, fill=fill, anchor="lm" if side > 0 else "rm")


@functools.lru_cache(maxsize=512)
def token(body, r):
    """Küçük, önceden çizilmiş gök cismi (aynı cisimden yüzlercesi çizilirken her seferinde hesaplanmasın)."""
    size = int(math.ceil(r * 2.4)) + 4
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    p = sphere_patch(body, size / 2, size / 2, r, 0.0)
    if p:
        img.alpha_composite(p[0], (p[1], p[2]))
    return img


def ring_pulse(layer, x, y, r, u, color=YELLOW, alpha=1.0):
    """Küçük cismi işaret eden nabız gibi atan halka."""
    d = ImageDraw.Draw(layer)
    for k in range(2):
        ph = (u * 2.2 + k * 0.5) % 1
        rad = r + 14 + ph * 70
        a = int(255 * alpha * (1 - ph))
        d.ellipse((x - rad, y - rad, x + rad, y + rad), outline=(*color, a), width=6)
    d.ellipse((x - r - 12, y - r - 12, x + r + 12, y + r + 12), outline=(*color, int(255 * alpha)), width=5)


class Compare:
    """Büyük cisim (ör. Jüpiter) ekranın ortasında, küçük cisim (ör. Dünya) hemen altında, ikisi de gerçek ölçekte.
    fill: küçükler büyüğün içini aşağıdan yukarı doldurur, sayaç hacim oranına ('count') kadar sayar.
    across: büyüğün çapı boyunca yan yana kaç küçük sığdığı. Dünya merkezi değil, büyük cismin merkezi = 0 km."""

    BIG_Y, BIG_R = 1060, 450           # tam görünümde büyük cismin ekrandaki merkezi ve yarıçapı (px)

    def __init__(self, spec):
        self.small, self.big = spec["small"], spec["big"]
        self.rs, self.rb = BODIES[self.small]["d"] / 2, BODIES[self.big]["d"] / 2
        self.count = spec.get("count")
        self.across = int(self.rb / self.rs)          # çap boyunca tam sığan sayı
        s = self.BIG_R / self.rb
        self.full = (s, (self.BIG_Y - H / 2) / s)
        rs_px = self.rs * s
        self.small_y = -(self.rb + max(80, rs_px + 60) / s + self.rs)   # küçük cisim büyüğün altında
        s_sp = 360 / self.rs
        self.spot = (s_sp, self.small_y)                                 # küçük cisim ekranın ortasında, büyük
        # doldurma: büyüğün diskine sığan küçük daireler (altıgen dizilim), aşağıdan yukarı; çok fazlaysa büyütülür
        r_tok = max(rs_px, self.BIG_R / math.sqrt(420 / 0.9))
        self.tok_r = r_tok
        pts, dy = [], r_tok * math.sqrt(3)
        row, y = 0, -self.BIG_R + r_tok
        while y <= self.BIG_R - r_tok:
            x = -self.BIG_R + r_tok + (r_tok if row % 2 else 0)
            while x <= self.BIG_R - r_tok:
                if math.hypot(x, y) + r_tok <= self.BIG_R * 0.985:
                    pts.append((x, y))
                x += 2 * r_tok
            y += dy
            row += 1
        self.fill_pts = sorted(pts, key=lambda p: (-p[1], p[0]))    # ekranda aşağıdan yukarı

    def zoomed(self, z):
        """Tam görünüm, büyük cismin merkezi sabit kalarak z kat yakınlaştırılmış."""
        s = self.full[0] * z
        return s, (self.BIG_Y - H / 2) / s

    def state(self, step, u, words=None):
        st = {"cam": self.full, "pulse": 0.0, "fill": 0.0, "fill_a": 0.0, "row": 0.0, "row_a": 0.0,
              "counter": None, "labels": 1.0, "big_label": 1.0}
        if step == "intro":   # kanca başlığı üstte durduğu için büyük cismin adı bu adımda yazılmaz
            st.update(cam=self.zoomed(lerp(1.12, 1.0, ease(u))), labels=span(u, 0.4, 0.8), big_label=0.0)
        elif step == "point":
            st.update(pulse=min(1.0, u * 5))
        elif step == "spot":
            st.update(cam=Lineup.cam_mix(self.full, self.spot, ease(span(u, 0.0, 0.75)), self.small_y),
                      labels=1 - span(u, 0, 0.3))
        elif step == "back":
            st.update(cam=Lineup.cam_mix(self.spot, self.full, ease(span(u, 0.0, 0.7)), self.small_y),
                      labels=span(u, 0.6, 1))
        elif step == "fill":
            f = span(u, 0.05, 0.85)
            st.update(fill=f, fill_a=1.0, counter=("fill", ease(f)))
        elif step == "across":
            st.update(fill=1.0, fill_a=1 - span(u, 0, 0.25), row=span(u, 0.15, 0.8), row_a=1.0,
                      counter=("across", span(u, 0.15, 0.8)))
        elif step == "outro":
            st.update(cam=self.zoomed(lerp(1.0, 1.15, ease(u))), row=1.0, row_a=1 - span(u, 0.5, 1))
        else:
            raise ValueError(f"compare şablonunda '{step}' adımı yok: {', '.join(TEMPLATES['compare'])}")
        return st

    def frame(self, step, u, words=None):
        st = self.state(step, u, words)
        s, cy = st["cam"]
        sy = lambda km: H / 2 - (km - cy) * s
        img = background().copy()
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))

        def put(patch):
            if patch:
                img.alpha_composite(patch[0], (patch[1], patch[2]))

        bx, by, br = W / 2, sy(0), self.rb * s
        put(sphere_patch(self.big, bx, by, br, 0.2))
        k = br / self.BIG_R                              # tam görünüme göre ölçek
        tr = self.tok_r * k
        if st["fill_a"] > 0 and st["fill"] > 0:
            pts = self.fill_pts
            shown = st["fill"] * len(pts)
            for i, (x, y) in enumerate(pts):
                p = min(1.0, shown - i)
                if p <= 0:
                    break
                self._token(img, bx + x * k, by + y * k, tr * ease_out_back(p), st["fill_a"])
        if st["row_a"] > 0 and st["row"] > 0:
            n = self.across
            r = self.rs * s
            for i in range(n):
                p = min(1.0, st["row"] * n - i)
                if p <= 0:
                    break
                x = bx - br + r * (2 * i + 1)
                self._token(img, x, by, r * ease_out_back(p), st["row_a"])
        sx, syy, sr = W / 2, sy(self.small_y), self.rs * s
        put(sphere_patch(self.small, sx, syy, sr, 0.0))
        if st["pulse"] > 0:
            ring_pulse(layer, sx, syy, sr, u, YELLOW, st["pulse"])
        if st["labels"] > 0.01:
            a = int(255 * st["labels"])
            ab = int(a * st["big_label"])
            if ab and br > 60 and -100 < by - br * 0.72 < H:
                text(layer, (bx, by - br * 0.72), BODIES[self.big]["name"].upper(), 44, fill=(255, 255, 255, ab))
            if -50 < syy < H + 50:
                text(layer, (sx + sr + 40, syy), BODIES[self.small]["name"].upper(), 38,
                     fill=(255, 255, 255, a), anchor="lm")
        if st["counter"]:
            kind, t = st["counter"]
            name = BODIES[self.small]["name"].upper()
            if kind == "fill" and self.count:
                num, label = f"{round(self.count * t):,}", f"{name}S INSIDE"
            elif kind == "across" and t > 0:
                num, label = f"{min(self.across, int(t * self.across + 0.999))}", f"{name}S ACROSS"
            else:
                num = None
            if num:
                text(layer, (W / 2, 250), num, 110, fill=YELLOW, stroke=8)
                text(layer, (W / 2, 335), label, 40)
        img.alpha_composite(layer)
        return img.convert("RGB")

    def _token(self, img, x, y, r, alpha):
        if r < 0.6 or not (-r < x < W + r and -r < y < H + r):
            return
        spr = token(self.small, round(r, 1))
        if alpha < 0.999:
            spr = spr.copy()
            spr.putalpha(spr.getchannel("A").point(lambda v: int(v * alpha)))
        img.alpha_composite(spr, (int(x - spr.size[0] / 2), int(y - spr.size[1] / 2)))


SIMS = {"lineup": Lineup, "compare": Compare}


def validate(spec, steps):
    """topics.json'daki sim tarifinin hataları (liste)."""
    errs = []
    kind = (spec or {}).get("type")
    if kind not in SIMS:
        return [f"sim.type '{kind}' bilinmiyor (olanlar: {', '.join(SIMS)})"]
    for s in steps:
        if s not in TEMPLATES[kind]:
            errs.append(f"'sim:{s}' adımı {kind} şablonunda yok ({', '.join(TEMPLATES[kind])})")
    if kind == "compare":
        for k in ("small", "big"):
            if spec.get(k) not in BODIES:
                errs.append(f"sim.{k} '{spec.get(k)}' tanımlı bir gök cismi değil ({', '.join(BODIES)})")
        if not errs and BODIES[spec["small"]]["d"] >= BODIES[spec["big"]]["d"]:
            errs.append("sim.small, sim.big'den küçük olmalı")
        if "fill" in steps and not isinstance(spec.get("count"), (int, float)):
            errs.append("fill adımı için sim.count (hacim oranı) sayı olmalı")
    if kind == "lineup":
        for b in [spec.get("bottom", "earth"), spec.get("top", "moon")] + list(spec.get("stack") or []):
            if b not in BODIES:
                errs.append(f"'{b}' tanımlı bir gök cismi değil ({', '.join(BODIES)})")
        if not spec.get("stack"):
            errs.append("sim.stack boş")
        for k in ("near_km", "far_km"):
            if not isinstance(spec.get(k), (int, float)):
                errs.append(f"sim.{k} sayı olmalı")
    return errs


def render_step(spec, step, seconds, path, vf="format=yuv420p", words=None):
    """Bir sahneyi (adımı) verilen sürede mp4 olarak yazar. words: [(sahne içi 0-1 an, kelime)], ör. gezegen
    adı söylendiği anda o gezegen ekrana gelir."""
    sim = SIMS[spec["type"]](spec)
    n = max(1, round(seconds * FPS))
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-vf", vf, "-t", f"{seconds:.3f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(n):
            p.stdin.write(sim.frame(step, i / max(n - 1, 1), words).tobytes())
    finally:
        p.stdin.close()
        p.wait()
    if p.returncode:
        raise RuntimeError(f"sim sahnesi yazılamadı: {step}")


def still(spec, step, u, path):
    """Tek kare (önizleme/test için)."""
    SIMS[spec["type"]](spec).frame(step, u).save(path)
