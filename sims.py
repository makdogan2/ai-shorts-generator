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
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
TW, TH = 2048, 1024            # doku çözünürlüğü (eşdikdörtgen harita)
LIGHT = np.array([-0.55, 0.36, 0.75])      # sol üstten, biraz yandan: kürelerde gölge sınırı görünür
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
TEMPLATES = {"collapse": ("intro", "size", "crush", "horizon", "compare", "outro"),
             "giant": ("intro", "first", "orbit", "zoom", "grow", "outro"),
             "lineup": ("intro", "stack", "measure", "overflow", "stretch", "spare"),
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


def streaks(seed, base=6, octaves=6, stretch=6):
    """Doğu-batı yönünde uzamış gürültü: gaz devlerinin rüzgârla çekilmiş bulut şeritleri için."""
    n = fbm(seed, base=base, octaves=octaves, h=TH, w=TW // stretch)
    return _up(n, TH, TW)


def _z(n):
    """Gürültüyü ortalaması 0, yayılımı 1 olacak şekilde ölçekler (fbm değerleri 0,5 civarında toplanır;
    böyle olmadan dalgalanma ve bulut eşikleri neredeyse hiç işlemez)."""
    return (n - n.mean()) / (n.std() + 1e-6)


def _bands(lat, stops):
    """Enleme göre renk: stops = [(enlem derece, (r, g, b)), ...] küçükten büyüğe; aradakiler yumuşak geçer."""
    deg = np.degrees(lat)
    xs = [d for d, _ in stops]
    return np.dstack([np.interp(deg, xs, [c[i] for _, c in stops]) for i in range(3)]).astype(np.float32)


def relief(h, strength):
    """Yükseklik haritasından ışığa göre gölge çarpanı (ışık sol üstten): kraterin ışığa bakan duvarı aydınlık,
    öbür duvarı gölgeli olur. Doku dönerken ışık yönü çok az kaydığı için gölge dokuya işlenir."""
    dx = (np.roll(h, -1, axis=1) - np.roll(h, 1, axis=1)) * 0.5
    dy = (np.roll(h, 1, axis=0) - np.roll(h, -1, axis=0)) * 0.5        # yukarı = kuzey
    dy[0], dy[-1] = 0, 0
    return np.clip(1 + strength * (-LIGHT[0] * dx - LIGHT[1] * dy) / LIGHT[2], 0.35, 1.6)[..., None]


def _crater_field(seed, count, rmin, rmax):
    """Krater yükseklik haritası (piksel biriminde): kase biçimli taban, yuvarlak kenar, büyüklerde merkez tepe.
    Derinlik yarıçapla orantılı (gerçek kraterlerde derinlik/çap ~0,1-0,2); küçük kraterler çok daha sık."""
    rng = np.random.default_rng(seed)
    h = np.zeros((TH, TW), np.float32)
    for _ in range(count):
        lat0 = math.asin(rng.uniform(-0.97, 0.97))
        lon0 = rng.uniform(-math.pi, math.pi)
        rad = rmin * (rmax / rmin) ** (rng.random() ** 2.2)
        rpx = rad / math.pi * TH
        rows = int(rad * 1.6 / math.pi * TH) + 2
        cols = int(rad * 1.6 / max(math.cos(lat0), 0.15) / (2 * math.pi) * TW) + 2
        r0 = int((0.5 - lat0 / math.pi) * TH)
        c0 = int((lon0 + math.pi) / (2 * math.pi) * TW)
        rr = np.arange(r0 - rows, r0 + rows + 1)
        cc = np.arange(c0 - cols, c0 + cols + 1)
        rr = rr[(rr >= 0) & (rr < TH)]
        if not len(rr):
            continue
        lat = (0.5 - (rr + 0.5) / TH) * math.pi
        lon = (cc + 0.5) / TW * 2 * math.pi - math.pi
        dl = (lon[None, :] - lon0 + math.pi) % (2 * math.pi) - math.pi
        d = np.sqrt((dl * math.cos(lat0)) ** 2 + (lat[:, None] - lat0) ** 2) / rad
        depth = 0.22 * rpx
        prof = np.where(d < 1, d * d - 1, 0) * depth + np.exp(-((d - 1) / 0.22) ** 2) * depth * 0.35
        if rad > rmax * 0.45:
            prof += np.exp(-(d / 0.16) ** 2) * depth * 0.35
        h[np.ix_(rr, cc % TW)] += prof.astype(np.float32)
    return h


@functools.lru_cache(maxsize=None)
def surface(body):
    """Gök cisminin yüzey haritası (TH x TW x 3, 0-1) ve parlama maskesi (okyanus; yoksa None).
    Tamamen kodla üretilir, telifsiz."""
    lat = _lat()
    spec = None
    if body == "sun":
        gran = _z(fbm(61, base=360, octaves=2))                 # granülasyon: çok küçük, sık hücreler
        m = _z(fbm(67, base=6, octaves=4))
        img = _mix((1.0, 0.62, 0.16), (1.0, 0.90, 0.58), 0.55 + 0.10 * gran + 0.12 * m)
        spots = fbm(71, base=14, octaves=3)
        band = np.exp(-((np.abs(lat) - 0.30) / 0.12) ** 2)      # lekeler orta enlemlerde
        img = _mix(img, (0.35, 0.12, 0.03), np.clip((_z(spots) - 2.6) * 3, 0, 1) * band)
    elif body == "earth":
        lon = _lon()
        warp = fbm(2, base=4, octaves=4)
        n = fbm(3, base=5, octaves=8)
        n = n + 0.10 * (warp - 0.5)
        land = n > np.quantile(n, 0.70)
        depth = np.clip((np.quantile(n, 0.70) - n) / 0.18, 0, 1)
        ocean = _mix((0.20, 0.54, 0.82), (0.07, 0.26, 0.58), depth ** 0.6)       # sığ sular açık mavi
        g = fbm(5, base=8, octaves=6)                      # nem: ormanla çölü ayırır
        al = np.abs(lat)
        wet = np.clip(0.55 + 0.35 * _z(g), 0, 1)
        forest = _mix((0.36, 0.38, 0.16), (0.06, 0.24, 0.07), wet)            # savan -> tropik orman
        dry = _mix((0.80, 0.66, 0.44), (0.56, 0.48, 0.26), wet)               # kum çölü -> bozkır
        temperate = _mix((0.40, 0.42, 0.22), (0.12, 0.32, 0.10), wet)
        tundra = _mix((0.42, 0.42, 0.36), (0.20, 0.26, 0.16), wet)
        ground = _mix(forest, dry, np.clip((al - 0.24) / 0.12, 0, 1) * np.clip(1.25 - 1.6 * wet, 0, 1))   # çöl yalnızca kuru yerde
        ground = _mix(ground, temperate, np.clip((al - 0.62) / 0.12, 0, 1))
        ground = _mix(ground, tundra, np.clip((al - 0.98) / 0.12, 0, 1))
        rid = 1 - np.abs(2 * fbm(4, base=14, octaves=6) - 1)                  # sırt gürültüsü: sıradağlar
        mount = np.clip((rid - 0.78) * 5, 0, 1) * np.clip((n - np.quantile(n, 0.72)) * 30, 0, 1)
        ground = _mix(ground, (0.42, 0.37, 0.31), mount * 0.8)
        ground = _mix(ground, (0.92, 0.93, 0.95), np.clip((mount - 0.6) * 3, 0, 1) * np.clip((al - 0.45) * 3, 0, 1))
        smooth = fbm(3, base=5, octaves=5)                 # kıta şeklinin yumuşak hali: tümsek tümsek olmasın
        hgt = np.where(land, smooth * 10 + mount * 9, np.quantile(smooth, 0.70) * 10)
        ground = ground * relief(hgt, 0.8)
        img = np.where(land[..., None], ground, ocean)
        ice = (np.abs(lat) - 1.20 - 0.22 * (g - 0.5)) * 14
        img = _mix(img, (0.93, 0.95, 0.98), ice)
        spec = np.where(land, 0.0, 1.0).astype(np.float32) * np.clip(1 - ice, 0, 1)
        c = _z(fbm(7, base=6, octaves=8))
        cs = _z(streaks(9, base=6, octaves=6, stretch=3))
        cf = _z(fbm(8, base=40, octaves=3))
        cl = 0.55 * c + 0.45 * cs + 0.25 * cf
        clouds = np.clip((cl + 0.28) * 0.70, 0, 1) ** 1.3
        clouds = clouds * np.clip(0.8 + 0.25 * _z(fbm(10, base=90, octaves=2)), 0, 1)   # bulut kenarında ince doku
        img = img * (1 - 0.35 * np.roll(clouds, 6, axis=1)[..., None])          # bulut gölgesi
        img = _mix(img, (0.98, 0.99, 1.0), clouds)
        spec = spec * (1 - clouds)
    elif body in ("moon", "mercury"):
        seed = 11 if body == "moon" else 13
        n = fbm(seed, base=6, octaves=8)
        tint = np.asarray((0.68, 0.67, 0.66) if body == "moon" else (0.60, 0.56, 0.52), np.float32)
        img = tint * (0.86 + 0.32 * (n - 0.5))[..., None]
        maria = np.zeros_like(n)
        if body == "moon":    # denizler: koyu, düz bazalt ovaları; içlerinde krater az
            m = fbm(17, base=3, octaves=5)
            maria = np.clip((m - np.quantile(m, 0.66)) * 12, 0, 1)
            img = _mix(img, (0.27, 0.27, 0.30), maria * 0.9)
        h = _crater_field(23 if body == "moon" else 29, 900 if body == "moon" else 1300, 0.006, 0.09)
        h = h * (1 - 0.75 * maria) + 6 * _z(fbm(seed + 2, base=24, octaves=4)) * (1 - 0.6 * maria)
        img = img * relief(h, 0.9)
    elif body == "venus":
        cs = streaks(31, base=4, octaves=6, stretch=4)
        n = fbm(33, base=5, octaves=6)
        s = np.sin(np.abs(lat) * 6 + 5 * cs + 2 * n)          # Y biçimli, çekilmiş bulut kuşakları
        img = _mix((0.97, 0.90, 0.70), (0.82, 0.66, 0.42), s * 0.4 + 0.45)
        img = _mix(img, (0.99, 0.95, 0.82), np.exp(-(lat / 0.35) ** 2) * 0.3)
    elif body == "mars":
        n = fbm(37, base=5, octaves=8)
        dark = fbm(39, base=3, octaves=5)
        img = _mix((0.82, 0.45, 0.24), (0.62, 0.32, 0.17), n * 1.2 - 0.1)
        img = _mix(img, (0.34, 0.19, 0.12), np.clip((dark - np.quantile(dark, 0.66)) * 10, 0, 1) * 0.85)
        h = _crater_field(41, 260, 0.006, 0.06) * 0.6 + 3 * _z(fbm(43, base=16, octaves=5))
        img = img * relief(h, 0.7)
        cap = (np.abs(lat) - 1.28 - 0.08 * (n - 0.5)) * 18
        img = _mix(img, (0.96, 0.95, 0.94), cap)
    elif body == "jupiter":
        sw = _z(streaks(41, base=6, octaves=7, stretch=3))      # bant kenarlarını dalgalandıran rüzgâr
        ed = _z(fbm(42, base=28, octaves=4))                    # küçük girdaplar
        fine = streaks(43, base=14, octaves=5, stretch=6)
        la = lat + 0.030 * sw + 0.010 * ed
        stops = [(-90, (0.55, 0.52, 0.50)), (-60, (0.62, 0.55, 0.48)), (-40, (0.80, 0.68, 0.54)),
                 (-30, (0.70, 0.52, 0.38)), (-24, (0.93, 0.86, 0.74)), (-19, (0.66, 0.42, 0.28)),
                 (-9, (0.62, 0.40, 0.27)), (-6, (0.95, 0.89, 0.78)), (5, (0.94, 0.86, 0.72)),
                 (8, (0.60, 0.38, 0.25)), (17, (0.66, 0.44, 0.30)), (20, (0.93, 0.87, 0.76)),
                 (28, (0.74, 0.58, 0.44)), (34, (0.88, 0.80, 0.68)), (45, (0.72, 0.62, 0.52)),
                 (60, (0.62, 0.56, 0.50)), (90, (0.52, 0.50, 0.50))]
        img = _bands(la, stops)
        img = img * (1 + 0.07 * _z(fine))[..., None]
        lon = _lon()
        # Büyük Kırmızı Leke (yaklaşık 22 derece güney) ve etrafında açık renkli halka
        d = np.sqrt(((lon - 0.4) / 0.20) ** 2 + ((lat + 0.39) / 0.07) ** 2)
        swirl = 0.5 + 0.5 * np.sin(d * 9 - np.arctan2(lat + 0.39, lon - 0.4) * 2 + 3 * fine)
        img = _mix(img, (0.96, 0.88, 0.78), np.exp(-((d - 1.25) / 0.25) ** 2) * 0.8)
        img = _mix(img, (0.78, 0.34, 0.20) * (0.85 + 0.25 * swirl)[..., None], np.clip((1.05 - d) * 5, 0, 1))
        for k in range(6):   # beyaz ovaller
            lo0, la0 = -2.6 + k * 0.9, -0.62 + 0.02 * (k % 2)
            dd = np.sqrt(((lon - lo0) / 0.05) ** 2 + ((lat - la0) / 0.025) ** 2)
            img = _mix(img, (0.97, 0.95, 0.90), np.exp(-dd ** 2) * 0.9)
    elif body == "saturn":
        sw = streaks(45, base=5, octaves=6, stretch=5)
        la = lat + 0.012 * _z(sw)
        stops = [(-90, (0.62, 0.60, 0.55)), (-50, (0.80, 0.72, 0.56)), (-30, (0.90, 0.80, 0.60)),
                 (-15, (0.86, 0.74, 0.54)), (0, (0.96, 0.88, 0.68)), (15, (0.88, 0.76, 0.55)),
                 (30, (0.92, 0.83, 0.63)), (50, (0.78, 0.70, 0.55)), (75, (0.60, 0.62, 0.62)), (90, (0.55, 0.58, 0.60))]
        img = _bands(la, stops) * (1 + 0.03 * _z(sw))[..., None]
    elif body == "uranus":
        sw = streaks(47, base=4, octaves=5, stretch=6)
        img = _mix((0.70, 0.90, 0.93), (0.58, 0.82, 0.88), np.sin(lat * 6 + 0.6 * sw) * 0.25 + 0.45)
        img = _mix(img, (0.80, 0.95, 0.96), np.clip((lat - 0.9) * 2, 0, 1) * 0.6)     # parlak kutup başlığı
    elif body == "neptune":
        sw = streaks(53, base=5, octaves=6, stretch=4)
        la = lat + 0.035 * _z(sw)
        img = _mix((0.30, 0.50, 0.94), (0.14, 0.26, 0.70), np.sin(la * 9) * 0.35 + 0.5)
        img = img * (1 + 0.05 * _z(streaks(54, base=12, octaves=4, stretch=6)))[..., None]
        lon = _lon()
        spot = np.sqrt(((lon + 0.6) / 0.16) ** 2 + ((lat + 0.35) / 0.07) ** 2)
        img = _mix(img, (0.08, 0.12, 0.40), np.clip((1.1 - spot) * 3, 0, 1))
        cirrus = streaks(55, base=8, octaves=5, stretch=10)
        img = _mix(img, (0.92, 0.96, 1.0), np.clip((_z(cirrus) - 1.0) * 1.5, 0, 1)
                   * (np.exp(-((lat + 0.30) / 0.06) ** 2) + np.exp(-((lat - 0.50) / 0.08) ** 2)))
    else:
        raise ValueError(f"bilinmeyen gök cismi: {body}")
    return np.clip(img, 0, 1).astype(np.float32), spec


def texture(body):
    return surface(body)[0]


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
    tex, spec = surface(body)
    # çift doğrusal örnekleme: gezegen dönerken doku pikselleri titremez
    fu = ((lon / (2 * math.pi) + 0.5) % 1) * TW - 0.5
    fv = np.clip((0.5 - lat / math.pi) * TH - 0.5, 0, TH - 1.001)
    u0 = np.floor(fu).astype(np.int32)
    v0 = np.floor(fv).astype(np.int32)
    au, av = (fu - u0)[..., None], (fv - v0)[..., None]
    u0 %= TW
    u1 = (u0 + 1) % TW
    v1 = np.minimum(v0 + 1, TH - 1)
    col = (tex[v0, u0] * (1 - au) + tex[v0, u1] * au) * (1 - av) + (tex[v1, u0] * (1 - au) + tex[v1, u1] * au) * av
    ndl = x * LIGHT[0] - y * LIGHT[1] + z * LIGHT[2]
    diffuse = np.clip(ndl, 0, 1)
    # yumuşak gölge sınırı + çok az ortam ışığı (gece tarafı tamamen siyah olmasın)
    shade = 0.04 + 0.96 * np.clip((ndl + 0.12) / 1.12, 0, 1) ** 0.95
    if star:   # yıldız kendi ışığını verir: kenara doğru kararır ve kızarır (limb darkening)
        diffuse = np.ones_like(z)
        mu = np.sqrt(z)
        col = col * (0.64 + 0.36 * mu)[..., None] * np.dstack([np.ones_like(z), 0.86 + 0.14 * mu, 0.72 + 0.28 * mu])
        shade = np.ones_like(z)
    if info.get("gas"):
        shade *= 0.62 + 0.38 * z ** 0.6
    if info.get("rings"):   # halkanın gezegene düşen gölgesi: yüzeyden ışığa giden ışın halka düzlemini nerede kesiyor?
        ct = math.sqrt(1 - RING_TILT ** 2)
        nx, ny, nz = 0.0, -ct, RING_TILT                       # halka düzleminin normali (ekran: y aşağı, z bize)
        lx, ly, lz = LIGHT[0], -LIGHT[1], LIGHT[2]
        ndl_r = ny * ly + nz * lz
        if abs(ndl_r) > 1e-4:
            t = -(ny * y + nz * z) / ndl_r
            qx, qy, qz = x + t * lx, y + t * ly, z + t * lz
            dist = np.sqrt(qx * qx + qy * qy + qz * qz)
            op = np.zeros_like(dist)
            for lo, hi, o in RING_BANDS:
                op = np.where((dist >= lo) & (dist <= hi), o, op)
            shade = shade * (1 - 0.7 * op * (t > 0) * (r2 < 1))
    col = col * shade[..., None]
    if spec is not None:   # okyanusta güneş parıltısı
        hx, hy, hz = LIGHT[0], LIGHT[1], LIGHT[2] + 1.0
        hn = math.sqrt(hx * hx + hy * hy + hz * hz)
        ndh = np.clip((x * hx - y * hy + z * hz) / hn, 0, 1)
        sm = (spec[v0, u0] * (1 - au[..., 0]) + spec[v0, u1] * au[..., 0])
        glint = sm * ndh ** 160 * 0.38 * (diffuse > 0)
        col = col + glint[..., None] * np.asarray((1.0, 0.95, 0.85), np.float32)
    if info.get("atmo") and not star:
        rim = (1 - z) ** 3 * np.clip(diffuse + 0.2, 0, 1) * 0.6
        col = col + rim[..., None] * np.asarray(info["atmo"], np.float32)
    alpha = np.clip((1 - rr) * r + 0.5, 0, 1)
    if halo:
        lit = 1.0 if star else np.clip((x * LIGHT[0] - y * LIGHT[1]) / np.maximum(rr, 1e-4) * 0.6 + 0.55, 0.15, 1)
        h = np.where(rr > 1, np.exp(-(rr - 1) / (halo * 0.35)) * (0.62 if star else 0.55) * lit, 0)
        h = h * np.clip((1 + halo - rr) / (halo * 0.4), 0, 1)   # kutunun kenarına varmadan sıfıra iner (köşeli iz kalmasın)
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
LABEL_GAP = 44      # küçük cisim etiketleri arasındaki en az dikey boşluk (px)


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
    # ince halkacıklar: birkaç frekansın üst üste binmesi, düzenli çizgi gibi durmasın
    a *= 0.78 + 0.10 * np.sin(rr * 90) + 0.07 * np.sin(rr * 233 + 1.7) + 0.05 * np.sin(rr * 517 + 0.4)
    shade = 0.90 - 0.14 * np.clip(x / 2.3, -1, 1)             # ışık soldan
    inner = np.clip((rr - 1.24) / 0.3, 0, 1)                  # C halkası daha gri ve koyu
    col = np.dstack([(0.62 + 0.28 * inner) * shade, (0.58 + 0.24 * inner) * shade,
                     (0.52 + 0.14 * inner) * shade]) * 255
    if not front:   # arka yarı: gezegen diskinin içinde kalan kısım görünmez (ön yarı gezegenin üstüne çizilir)
        a = np.where(x * x + y * y < 1, 0, a)
        # gezegenin halkaya düşen gölgesi (ışık sol önden geldiği için sağ arkada)
        sh = np.clip(((x - 0.55) ** 2 + ((y + 0.02) / RING_TILT * 0.9) ** 2 - 1.0) * 6, 0, 1)
        col = col * (0.30 + 0.70 * sh)[..., None]
    rgba = np.dstack([col, np.clip(a, 0, 1) * 255]).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA"), x0, y0


BG_PAD = 1.12   # arka plan ekrandan biraz büyük üretilir: kamera yaklaşıp uzaklaşırken yıldızlar hafifçe kayar


@functools.lru_cache(maxsize=4)
def background(seed=1):
    """Yıldızlı uzay (ekrandan BG_PAD kat büyük): parlaklığı ve rengi değişen binlerce yıldız, çapraz uzanan
    soluk bir Samanyolu kuşağı ve çok hafif bulutsu."""
    bw, bh = int(W * BG_PAD), int(H * BG_PAD)
    rng = np.random.default_rng(seed)
    neb = fbm(seed + 100, base=3, octaves=5, h=bh // 4, w=bw // 4)
    neb2 = fbm(seed + 200, base=2, octaves=4, h=bh // 4, w=bw // 4)
    dust = fbm(seed + 300, base=10, octaves=5, h=bh // 4, w=bw // 4)
    yy, xx = np.mgrid[0:bh // 4, 0:bw // 4].astype(np.float32)
    # Samanyolu: ekranı çapraz kesen geniş, yumuşak kuşak; içinde koyu toz damarları
    band = np.exp(-(((xx * 0.55 - yy * 0.30) - bw / 4 * 0.12) / (bw / 4 * 0.22)) ** 2)
    img = np.zeros((bh // 4, bw // 4, 3), np.float32)
    img += np.clip(neb - 0.45, 0, 1)[..., None] * np.array([0.30, 0.12, 0.45]) * 0.55
    img += np.clip(neb2 - 0.5, 0, 1)[..., None] * np.array([0.05, 0.20, 0.45]) * 0.55
    img += (band * (0.55 + 0.9 * (dust - 0.5)) * (1 - 0.6 * np.clip((dust - 0.55) * 4, 0, 1)))[..., None] \
        * np.array([0.11, 0.10, 0.13])
    img += np.array([0.012, 0.012, 0.03])
    base = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).resize((bw, bh), Image.BICUBIC)
    d = ImageDraw.Draw(base)
    pal = [(255, 255, 255), (200, 220, 255), (255, 236, 215), (255, 214, 180)]
    bandf = Image.fromarray((band * 255).astype(np.uint8)).resize((bw, bh))
    for _ in range(3200):
        x, y = int(rng.integers(0, bw)), int(rng.integers(0, bh))
        if rng.random() > 0.35 + 0.65 * bandf.getpixel((x, y)) / 255:   # kuşakta yıldız daha sık
            continue
        b = int(255 * min(1.0, 0.25 + rng.pareto(3.0) * 0.35))           # çoğu sönük, birkaçı parlak
        t = pal[int(rng.integers(0, len(pal)))]
        c = tuple(int(v * b / 255) for v in t)
        if b < 200:
            d.point((x, y), fill=c)
        else:
            d.ellipse((x - 1, y - 1, x + 1, y + 1), fill=c)
    for _ in range(22):
        x, y = rng.integers(0, bw), rng.integers(0, bh)
        for rad, a in ((8, 30), (4, 80), (2, 255)):
            d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=(a, a, a))
    return base.convert("RGBA")


@functools.lru_cache(maxsize=24)
def _backdrop(m):
    big = background()
    cw, ch = W * BG_PAD / m, H * BG_PAD / m
    x0, y0 = (big.size[0] - cw) / 2, (big.size[1] - ch) / 2
    view = big.convert("RGB").resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch))
    return ImageChops.multiply(view, _vignette()).convert("RGBA")   # kenar kararması arka plana işlenir


def backdrop(zoom=1.0):
    """Kameranın yakınlığına göre çok az büyüyen arka plan (uzak yıldızlarda derinlik hissi).
    zoom: şablonun tam görünümüne göre kamera ölçeği."""
    m = min(max(1.1 * max(zoom, 1e-6) ** 0.04, 1.0), 1.4)
    return _backdrop(round(m, 3)).copy()


@functools.lru_cache(maxsize=1)
def _vignette():
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - W / 2) / (W * 0.62)) ** 2 + ((yy - H / 2) / (H * 0.62)) ** 2)
    v = 1 - 0.28 * np.clip(r - 0.55, 0, 1) ** 1.5
    return Image.fromarray((v * 255).astype(np.uint8), "L").convert("RGB")


def glow(img):
    """Sahneye ışık parlaması: parlak yerler (Güneş, aydınlık bulutlar) hafifçe taşar. Yazılar bundan sonra çizilir."""
    rgb = img.convert("RGB")
    small = rgb.resize((W // 4, H // 4), Image.BILINEAR)
    a = np.asarray(small, np.float32)
    lum = a.max(axis=2, keepdims=True)
    bright = Image.fromarray((np.clip((lum - 205) / 50, 0, 1) * a * 0.6).astype(np.uint8))
    if not bright.getbbox():          # parlak yer yoksa iş yok
        return rgb
    halo = bright.filter(ImageFilter.GaussianBlur(10)).resize((W, H), Image.BILINEAR)
    return ImageChops.add(rgb, halo)


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
        img = backdrop(s / self.full[0])
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
        img = glow(img)                       # RGB döner; yazı katmanı üstüne maskeyle yapıştırılır
        img.paste(layer, (0, 0), layer)
        return img

    def labels(self, layer, st, sy, s):
        a = st["labels"]
        items = [(self.bottom, sy(0), self.rb * s, 1.0, None), (self.top, sy(st["moon"]), self.rt * s, 1.0, None)]
        # aynı cisim art arda dizildiyse (ör. 30 Dünya) tek etiket: grubun ortasında "30 EARTHS"
        k = 0
        while k < len(self.stack):
            j = k
            while j + 1 < len(self.stack) and self.stack[j + 1] == self.stack[k]:
                j += 1
            b, n = self.stack[k], j - k + 1
            shown = [m for m in range(k, j + 1) if st["pop"][m] > 0.3]
            if shown:
                mid = shown[len(shown) // 2]
                y = sy(self.centers[mid])
                r = BODIES[b]["d"] / 2 * s * st["pop"][mid]
                label = f"{len(shown)} {BODIES[b]['name'].upper()}S" if n > 1 else None
                items.append((b, y, r, 1.0, label))
            k = j + 1
        small = []
        for b, y, r, p, label in items:
            if p < 0.3 or not -50 < y < H + 50:
                continue
            fill = (255, 255, 255, int(255 * a * min(1, p)))
            name = label or BODIES[b]["name"].upper()
            if r > 75:   # büyük gezegen: adı üstünde
                text(layer, (W / 2, y), name, 40, fill=fill)
            else:
                small.append([y, b, r, fill, name])
        # küçük cisimler: ekranda yukarıdan aşağı sırayla bir sola bir sağa; aynı taraftakiler üst üste binmesin
        small.sort(key=lambda e: e[0])
        d = ImageDraw.Draw(layer)
        for side in (-1, 1):
            group = [e for k, e in enumerate(small) if (1 if k % 2 else -1) == side]
            ys = [e[0] for e in group]
            for _ in range(20):                      # etiketleri aralarında en az GAP piksel kalana kadar it
                moved = False
                for k in range(1, len(ys)):
                    over = LABEL_GAP - (ys[k] - ys[k - 1])
                    if over > 0:
                        ys[k - 1] -= over / 2
                        ys[k] += over / 2
                        moved = True
                if not moved:
                    break
            for (y, b, r, fill, name), ly in zip(group, ys):
                x_edge = W / 2 + side * (r + 8)
                x_txt = W / 2 + side * (r + 70 + (30 if abs(ly - y) > 2 else 0))
                d.line((x_edge, y, x_txt - side * 22, ly, x_txt - side * 10, ly),
                       fill=(255, 255, 255, int(160 * a)), width=3, joint="curve")
                text(layer, (x_txt, ly), name, 34, fill=fill, anchor="lm" if side > 0 else "rm")


@functools.lru_cache(maxsize=1)
def _scrim(w=620, h=300):
    """Sayacın arkasındaki yumuşak koyu zemin: yıldızların ve gezegenin üstünde rakam net okunsun."""
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).ellipse((w * 0.12, h * 0.15, w * 0.88, h * 0.85), fill=150)
    m = m.filter(ImageFilter.GaussianBlur(40))
    img = Image.new("RGBA", (w, h), (3, 4, 14, 0))
    img.putalpha(m)
    return img


@functools.lru_cache(maxsize=4096)
def token(body, r, rot=0.0):
    """Küçük, önceden çizilmiş gök cismi (aynı cisimden yüzlercesi çizilirken her seferinde hesaplanmasın).
    rot: dönüş; doldurmada her kopya farklı yüzünü gösterir, birebir aynı yapıştırma gibi durmaz."""
    size = int(math.ceil(r * 2.4)) + 4
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if r >= 3:   # temas gölgesi: ışığın tersine (sağ alta) düşen yumuşak koyu leke; kopyalar yüzeye otursun
        sh = Image.new("L", (size, size), 0)
        ox, oy = size / 2 + r * 0.12, size / 2 + r * 0.10
        ImageDraw.Draw(sh).ellipse((ox - r * 1.02, oy - r * 1.02, ox + r * 1.02, oy + r * 1.02), fill=120)
        sh = sh.filter(ImageFilter.GaussianBlur(max(1.0, r * 0.12)))
        shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        shadow.putalpha(sh)
        img.alpha_composite(shadow)
    p = sphere_patch(body, size / 2, size / 2, r, rot)
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
            st.update(fill=f, fill_a=1.0, counter=("fill", ease(f), math.sin(math.pi * span(u, 0.85, 0.97))))
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
        img = backdrop(s / self.full[0])
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))

        def put(patch):
            if patch:
                img.alpha_composite(patch[0], (patch[1], patch[2]))

        bx, by, br = W / 2, sy(0), self.rb * s
        rings = BODIES[self.big].get("rings")
        if rings:   # halkalı gezegen (Satürn): arka yarı gezegenin arkasında, ön yarı içindekilerin önünde
            put(ring_patch(bx, by, br, front=False))
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
                self._token(img, bx + x * k, by + y * k, tr * ease_out_back(p), st["fill_a"], i)
        if st["row_a"] > 0 and st["row"] > 0:
            n = self.across
            r = self.rs * s
            for i in range(n):
                p = min(1.0, st["row"] * n - i)
                if p <= 0:
                    break
                x = bx - br + r * (2 * i + 1)
                self._token(img, x, by, r * ease_out_back(p), st["row_a"], i + 3)
        if rings:
            put(ring_patch(bx, by, br, front=True))
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
            kind, t = st["counter"][:2]
            bump = st["counter"][2] if len(st["counter"]) > 2 else 0.0
            name = BODIES[self.small]["name"].upper()
            if kind == "fill" and self.count:
                num, label = f"{round(self.count * t):,}", f"{name}S INSIDE"
            elif kind == "across" and t > 0:
                num, label = f"{min(self.across, int(t * self.across + 0.999))}", f"{name}S ACROSS"
            else:
                num = None
            if num:
                sc = _scrim()
                layer.alpha_composite(sc, (int(W / 2 - sc.size[0] / 2), int(300 - sc.size[1] / 2)))
                pop = 1 + 0.14 * bump               # sayaç son rakama varınca kısa bir büyüyüp oturma
                text(layer, (W / 2, 250), num, int(110 * pop), fill=YELLOW, stroke=8)
                text(layer, (W / 2, 335), label, 40)
                if kind == "fill":   # çizim düz bir kesit; sayı hacimden geliyor, izleyici yanılmasın
                    text(layer, (W / 2, 385), "BY VOLUME", 28, fill=(255, 255, 255, 190))
        img = glow(img)                       # RGB döner; yazı katmanı üstüne maskeyle yapıştırılır
        img.paste(layer, (0, 0), layer)
        return img

    def _token(self, img, x, y, r, alpha, var=0):
        if r < 0.6 or not (-r < x < W + r and -r < y < H + r):
            return
        spr = token(self.small, round(r, 1), (var * 7 % 6) * 1.05)
        if alpha < 0.999:
            spr = spr.copy()
            spr.putalpha(spr.getchannel("A").point(lambda v: int(v * alpha)))
        img.alpha_composite(spr, (int(x - spr.size[0] / 2), int(y - spr.size[1] / 2)))


# ---------------------------------------------------------------- kara delikler

RS_KM = 2.953          # 1 Güneş kütlesinin Schwarzschild yarıçapı (km); yarıçap kütleyle doğru orantılı büyür
ORBITS = [             # gezegen yörüngelerinin yarı büyük eksenleri (km), NASA gezegen bilgi sayfaları
    ("Mercury", 57.9e6), ("Venus", 108.2e6), ("Earth", 149.6e6), ("Mars", 227.9e6),
    ("Jupiter", 778.5e6), ("Saturn", 1432.0e6), ("Uranus", 2867.0e6), ("Neptune", 4515.0e6),
]
ORANGE = (255, 170, 70)


def horizon_km(mass_suns):
    """Olay ufkunun çapı (km)."""
    return 2 * RS_KM * mass_suns


def black_hole(img, cx, cy, r, disk=0.0, spin=0.0):
    """img (RGBA) üzerine r piksel yarıçaplı kara delik: arkadaki yıldız ışığı bükülür (kütleçekimsel mercek),
    ufkun hemen dışında ince parlak foton halkası, istenirse turuncu, dönen bir yığılma diski parıltısı.
    Siyah disk olay ufkunun gerçek boyutu; ölçü etiketleri bu çapı gösterir."""
    if r < 0.8:
        return img
    R = int(min(r * 6 + 40, 2600))
    x0, x1 = max(0, int(cx - R)), min(W, int(cx + R))
    y0, y1 = max(0, int(cy - R)), min(H, int(cy + R))
    if x0 >= x1 or y0 >= y1:
        return img
    src = np.asarray(img, np.float32)
    xs = np.arange(x0, x1, dtype=np.float32) + 0.5 - cx
    ys = np.arange(y0, y1, dtype=np.float32) + 0.5 - cy
    dx, dy = np.meshgrid(xs, ys)
    d = np.sqrt(dx * dx + dy * dy) + 1e-3
    re = r * 1.7                                         # Einstein yarıçapı: bu halkada ışık en çok bükülür
    ds = d - re * re / d                                 # ışığın aslında geldiği yer (negatifse karşı taraftan)
    fade = np.clip((R - d) / (R * 0.25), 0, 1)           # bölgenin kenarında bükülme sıfıra iner (dikiş olmasın)
    k = (ds * fade + d * (1 - fade)) / d
    sx = np.clip(cx + dx * k, 0, W - 1).astype(np.int32)
    sy = np.clip(cy + dy * k, 0, H - 1).astype(np.int32)
    out = src[sy, sx]
    rel = d / r
    # foton halkası ve ufka yakın parıltı
    ring = np.exp(-((rel - 1.06) / 0.05) ** 2) * 1.0 + np.exp(-((rel - 1.0) / 0.35) ** 2) * 0.25 * (rel > 1)
    out[..., :3] += ring[..., None] * np.array([255, 205, 150], np.float32) * 0.9
    if disk > 0:   # yığılma diski: eğik halka, ışık tarafı daha parlak (Doppler etkisine benzer), yavaşça dönüyor
        ang = np.arctan2(dy, dx)
        yy = dy / 0.28
        rd = np.sqrt(dx * dx + yy * yy) / r
        band = np.clip((rd - 1.25) * 3, 0, 1) * np.clip((4.2 - rd) / 1.5, 0, 1)
        swirl = 0.75 + 0.25 * np.sin(ang * 3 + rd * 4 - spin * 3)
        bright = (0.65 + 0.35 * np.cos(ang - math.pi)) * band * swirl * disk
        front = dy > 0                                   # ön yarı ufkun önünden geçer, arka yarı arkasında kalır
        col = np.array([255, 150, 60], np.float32)
        add = bright[..., None] * col * 1.1
        hole = rel < 1.0
        out[..., :3] = np.where((hole & ~front)[..., None], out[..., :3], out[..., :3] + add)
        out[..., :3] = np.where((hole & front)[..., None], out[..., :3] + add, out[..., :3])
    inside = np.clip((1.0 - rel) * r + 0.5, 0, 1)        # ufuk: kenarı yumuşatılmış siyah
    if disk > 0:
        inside = inside * (1 - (dy > 0) * np.clip(bright * 1.4, 0, 1))
    out[..., :3] *= (1 - inside)[..., None]
    patch = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")
    img.paste(patch, (x0, y0))
    return img


def km_label(km):
    """Okunur uzunluk: 5.9 km, 1,391,400 km, 24 million km, 38 billion km."""
    if km >= 1e9:
        return f"{km / 1e9:.0f} billion km"
    if km >= 1e7:
        return f"{km / 1e6:.0f} million km"
    if km >= 100:
        return f"{km:,.0f} km"
    return f"{km:.1f} km"


class Collapse:
    """Bir gök cismi (ör. Güneş) aynı kütleyle olay ufku boyutuna sıkışır. Kamera cisimle birlikte yaklaşır
    (ekranda 'ZOOM x' yazar). Sonunda kara delik ve gerçek ölçekte bir karşılaştırma (ör. Everest'in boyu).
    Adımlar: intro, size, crush, horizon, compare, outro"""

    CY = 1040

    def __init__(self, spec):
        self.body = spec.get("body", "sun")
        self.d0 = BODIES[self.body]["d"]
        self.mass = float(spec.get("mass_suns", 1.0))
        self.dh = horizon_km(self.mass)
        cmp_ = spec.get("compare") or {}
        self.cmp_name, self.cmp_km = cmp_.get("name"), cmp_.get("km")

    def state(self, step, u):
        st = {"d": self.d0, "rpx": 380.0, "zoom": 1.0, "ruler": 0.0, "hole": 0.0, "flash": 0.0, "cmp": 0.0,
              "label": 1.0, "spin": u}
        if step == "intro":
            st.update(rpx=lerp(400, 380, ease(u)), label=span(u, 0.5, 0.9))
        elif step == "size":
            st.update(ruler=ease(span(u, 0.05, 0.5)))
        elif step == "crush":
            t = ease(span(u, 0.05, 0.92))
            d = math.exp(lerp(math.log(self.d0), math.log(self.dh), t))
            rpx = lerp(380, 160, t)
            st.update(d=d, rpx=rpx, zoom=(self.d0 / d) * (rpx / 380), ruler=1.0, label=1 - span(u, 0, 0.2))
        elif step == "horizon":
            st.update(d=self.dh, rpx=160, zoom=(self.d0 / self.dh) * 160 / 380, ruler=1.0,
                      hole=span(u, 0.0, 0.25), flash=max(0.0, 1 - u * 4), label=0.0)
        elif step == "compare":
            t = ease(span(u, 0.0, 0.5))
            st.update(d=self.dh, rpx=lerp(160, 110, t), zoom=(self.d0 / self.dh) * lerp(160, 110, t) / 380,
                      ruler=1 - t, hole=1.0, cmp=t, label=0.0)
        elif step == "outro":
            st.update(d=self.dh, rpx=lerp(110, 150, ease(u)), zoom=(self.d0 / self.dh) * lerp(110, 150, ease(u)) / 380,
                      hole=1.0, cmp=1 - span(u, 0.0, 0.4), label=0.0)
        else:
            raise ValueError(f"collapse şablonunda '{step}' adımı yok: {', '.join(TEMPLATES['collapse'])}")
        return st

    def frame(self, step, u, words=None):
        st = self.state(step, u)
        img = backdrop(st["zoom"] ** 0.5)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cx, cy, r = W / 2 - st["cmp"] * W * 0.20, self.CY, st["rpx"]   # karşılaştırmada kara delik sola kayar
        if st["hole"] < 1:
            # sıkıştıkça ısınan yıldız: rengi beyaza döner, parlaklığı artar
            p = sphere_patch(self.body, cx, cy, r, u * 0.3)
            if p:
                heat = 1 - st["d"] / self.d0 if step == "crush" else (1.0 if st["hole"] > 0 else 0.0)
                patch = p[0]
                if heat > 0.01:
                    a = np.asarray(patch, np.float32)
                    a[..., :3] = a[..., :3] * (1 - 0.6 * heat) + np.array([235, 240, 255]) * 0.6 * heat
                    patch = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA")
                if st["hole"] > 0:
                    patch.putalpha(patch.getchannel("A").point(lambda v: int(v * (1 - st["hole"]))))
                img.alpha_composite(patch, (p[1], p[2]))
        if st["hole"] > 0:
            black_hole(img, cx, cy, r * st["hole"] ** 0.5)
        if st["flash"] > 0:
            fl = Image.new("RGBA", (W, H), (255, 255, 255, int(200 * st["flash"])))
            img.alpha_composite(fl)
        if st["cmp"] > 0 and self.cmp_km:
            # karşılaştırma: aynı ölçekte dağ silueti (yükseklik gerçek oran)
            px_per_km = 2 * r / self.dh
            hpx = self.cmp_km * px_per_km
            bx = W * 0.70
            base = cy + r
            a = int(255 * st["cmp"])
            d = ImageDraw.Draw(layer)
            pts = [(bx - hpx * 0.55, base), (bx - hpx * 0.14, base - hpx * 0.72), (bx, base - hpx),
                   (bx + hpx * 0.17, base - hpx * 0.7), (bx + hpx * 0.58, base)]
            d.polygon(pts, fill=(120, 128, 150, a))
            d.polygon([(bx - hpx * 0.14, base - hpx * 0.78), (bx, base - hpx), (bx + hpx * 0.17, base - hpx * 0.76),
                       (bx, base - hpx * 0.84)], fill=(240, 244, 255, a))
            ruler(layer, bx + hpx * 0.68, base, base - hpx, GREEN, st["cmp"])
            pill(layer, (bx, base - hpx - 70), f"{self.cmp_km:.1f} km", 40, GREEN, anchor="mm", alpha=st["cmp"])
            text(layer, (bx, base + 50), self.cmp_name.upper(), 34, fill=(255, 255, 255, a))
            ruler(layer, cx - r - 40, cy + r, cy - r, YELLOW, st["cmp"])
            pill(layer, (cx, cy - r - 70), km_label(self.dh), 40, YELLOW, alpha=st["cmp"])
            text(layer, (cx, cy + r + 50), "BLACK HOLE", 34, fill=(255, 255, 255, a))
        if st["ruler"] > 0:
            g = st["ruler"]
            d = ImageDraw.Draw(layer)
            yb = cy + r + 70
            c = (*YELLOW, int(255 * g))
            d.line((cx - r, yb, cx + r, yb), fill=c, width=6)
            for x in (cx - r, cx + r):
                d.line((x, yb - 22, x, yb + 22), fill=c, width=6)
            pill(layer, (cx, yb + 70), km_label(st["d"]), 46, YELLOW, alpha=g)
        if st["zoom"] > 1.5:
            text(layer, (W / 2, 330), f"ZOOM x{st['zoom']:,.0f}", 40, fill=(255, 255, 255, 200))
        if st["label"] > 0.01:
            text(layer, (cx, cy - r * 0.7), BODIES[self.body]["name"].upper(), 44,
                 fill=(255, 255, 255, int(255 * st["label"])))
        img = glow(img)
        img.paste(layer, (0, 0), layer)
        return img


class Giant:
    """Dev kara delikler güneş sisteminin yörüngeleriyle aynı ölçekte. holes: [{name, mass_suns}, ...] küçükten
    büyüğe. Adımlar: intro (en büyüğü, içinde Neptün'ün yörüngesi: kanca karesi), first (ilk kara delik iç gezegen
    yörüngeleriyle), orbit (içine sığdığı yörüngenin vurgusu), zoom (gezegenlerin yörüngeleri boyunca geri çekilme),
    grow (büyük kara delik merkezden büyür), outro"""

    def __init__(self, spec):
        holes = spec.get("holes") or []
        self.small, self.big = holes[0], holes[-1]
        self.rs_small = horizon_km(self.small["mass_suns"]) / 2
        self.rs_big = horizon_km(self.big["mass_suns"]) / 2
        self.fit = spec.get("fit_orbit", "Mercury")
        self.fit_km = dict(ORBITS)[self.fit]
        self.nep = dict(ORBITS)["Neptune"]
        self.s_inner = 360 / self.fit_km                  # iç görünüm: Merkür yörüngesi 360 px
        self.s_nep = 380 / self.nep                       # Neptün yörüngesi 380 px
        self.s_big = 430 / self.rs_big                    # büyük kara delik 430 px

    def state(self, step, u):
        st = {"s": self.s_big, "small": 0.0, "big": 1.0, "pulse": 0.0, "labels": 1.0, "pill": None}
        if step == "intro":
            st.update(s=self.s_big * lerp(1.06, 1.0, ease(u)))
        elif step == "first":
            st.update(s=self.s_inner, small=ease(span(u, 0.0, 0.3)), big=0.0,
                      pill=(km_label(2 * self.rs_small), span(u, 0.3, 0.6), self.small["name"]))
        elif step == "orbit":
            st.update(s=self.s_inner, small=1.0, big=0.0, pulse=min(1.0, u * 4),
                      pill=(km_label(2 * self.rs_small), 1.0, self.small["name"]))
        elif step == "zoom":
            t = ease(span(u, 0.0, 0.9))
            st.update(s=math.exp(lerp(math.log(self.s_inner), math.log(self.s_nep), t)), small=1 - span(u, 0.6, 0.9), big=0.0)
        elif step == "grow":
            t = ease(span(u, 0.0, 0.85))
            st.update(s=math.exp(lerp(math.log(self.s_nep), math.log(self.s_big), t)), big=t,
                      pill=(km_label(2 * self.rs_big), span(u, 0.5, 0.8), self.big["name"]))
        elif step == "outro":
            st.update(s=self.s_big * lerp(1.0, 1.08, ease(u)), pill=(km_label(2 * self.rs_big), 1 - span(u, 0, 0.4), self.big["name"]))
        else:
            raise ValueError(f"giant şablonunda '{step}' adımı yok: {', '.join(TEMPLATES['giant'])}")
        return st

    def frame(self, step, u, words=None):
        st = self.state(step, u)
        s = st["s"]
        img = backdrop(s / self.s_nep)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        cx, cy = W / 2, 1000
        # yörüngeler: ekranda görünür boydakiler çizilir, adları sağda
        big_view = step in ("intro", "outro") or (step == "grow" and u > 0.5)
        last_label = -1e9
        for name, a_km in ORBITS:
            r = a_km * s
            if big_view and name != "Neptune":
                continue
            if 14 < r < 1400:
                hl = name == self.fit and st["pulse"] > 0
                alpha = 150 if not hl else int(150 + 105 * (0.5 + 0.5 * math.sin(u * 14)))
                col = (*((YELLOW if hl else (150, 170, 220))), alpha)
                d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=5 if hl else 3)
                big_view = step in ("intro", "outro") or (step == "grow" and u > 0.5)
                show = (name == "Neptune") if big_view else (70 <= r < 520)
                if show and st["labels"] > 0 and (r - last_label) >= 40:   # birbirine çok yakın etiketler binmesin
                    last_label = r
                    lbl = f"{name.upper()}'S ORBIT" if (hl or big_view) else name.upper()
                    # etiket yörüngenin tepesinde, ortalı: hiçbir boyda ekrandan taşmaz
                    text(layer, (cx, cy - r - 26), lbl, 30 if not hl else 36, fill=(255, 255, 255, 220))
        # güneş (iç görünümde küçük parlak nokta) ya da kara delikler
        if st["small"] > 0:
            black_hole(img, cx, cy, self.rs_small * s * st["small"], disk=0.8, spin=u)
        else:
            sr = max(3.0, BODIES["sun"]["d"] / 2 * s)
            if sr < 60:
                d.ellipse((cx - sr, cy - sr, cx + sr, cy + sr), fill=(255, 220, 140, 255))
        if st["big"] > 0:
            black_hole(img, cx, cy, self.rs_big * s * st["big"], disk=1.0, spin=u)
        if st["pill"]:
            txt, a, name = st["pill"]
            if a > 0.01:
                pill(layer, (W / 2, 300), txt, 54, YELLOW, alpha=a)
                text(layer, (W / 2, 380), name.upper(), 40, fill=(255, 255, 255, int(255 * a)))
        img = glow(img)
        img.paste(layer, (0, 0), layer)
        return img


SIMS = {"lineup": Lineup, "compare": Compare, "collapse": Collapse, "giant": Giant}


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
    if kind == "collapse":
        if spec.get("body", "sun") not in BODIES:
            errs.append(f"sim.body '{spec.get('body')}' tanımlı bir gök cismi değil")
        if not isinstance(spec.get("mass_suns", 1.0), (int, float)):
            errs.append("sim.mass_suns sayı olmalı")
    if kind == "giant":
        holes = spec.get("holes") or []
        if len(holes) < 2 or any(not isinstance(h.get("mass_suns"), (int, float)) or not h.get("name") for h in holes):
            errs.append("sim.holes en az iki kara delik olmalı: [{name, mass_suns}, ...]")
        if spec.get("fit_orbit", "Mercury") not in dict(ORBITS):
            errs.append(f"sim.fit_orbit bir gezegen adı olmalı ({', '.join(n for n, _ in ORBITS)})")
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
