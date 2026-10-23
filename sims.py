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
  collapse "Kara deliğe sıkıştır": intro, size, crush, horizon, compare, outro
  giant    Dev kara delikler gezegen yörüngeleriyle: intro, first, orbit, zoom, grow, outro
  sky      Gökyüzünde gerçek açısal boyut (Ay'ın yerinde Jüpiter): intro, compare, grow, measure, outro
  engulf   Bir yörünge büyük bir cismin içinde (Ay'ın yörüngesi Güneş'te): intro, orbit, engulf, measure, outro
  map      Bir ülkenin ana hatları bir cismin üstünde aynı ölçekte: intro, place, measure, verdict, outro
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
             "compare": ("intro", "point", "spot", "back", "fill", "across", "outro"),
             "sky": ("intro", "compare", "grow", "measure", "outro"),
             "engulf": ("intro", "orbit", "engulf", "measure", "outro"),
             "map": ("intro", "place", "measure", "verdict", "outro")}

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
CAPTION_BAND = (800, 1120)   # altyazı şeridi (px, dikey): grup etiketleri buraya konmaz


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
                if label and CAPTION_BAND[0] < y < CAPTION_BAND[1]:
                    # grup etiketi ortadaki altyazı şeridine binmesin: grubun şeridin hemen üstünde ya da
                    # altında kalan bir üyesini göster (hepsi aynı cisim, ok yine gruba bakar)
                    lo, hi = sorted((sy(self.centers[shown[0]]), sy(self.centers[shown[-1]])))
                    for cand in (CAPTION_BAND[0], CAPTION_BAND[1]):
                        if lo <= cand <= hi:
                            y = cand
                            break
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
        # outro'daki sıra yalnızca across adımı kullanıldıysa görünür (yoksa büyüğün önünde tek bir kopya belirir)
        steps = spec.get("steps")
        self.show_row = steps is None or "across" in steps
        # across adımı dolu diskten başlar ve onu söndürür; fill adımı hiç kullanılmadıysa o dolu disk bir an
        # belirip kaybolmasın (ör. Güneş'in önünde 109 Dünya: önce spot/back, sonra across)
        self.show_fill = steps is None or "fill" in steps
        q = self.rs / self.rb
        if q > 0.2:
            # boyları yakın iki cisim (ör. Venüs ile Dünya): ikisi de ekrana tam sığsın diye büyüğü küçült,
            # üst kenarı aynı yerde kalsın (kanca başlığının altında), aradaki boşluk sabit 90 px
            top = self.BIG_Y - self.BIG_R
            self.BIG_R = min(self.BIG_R, (H - 80 - top - 90) / (2 + 2 * q))
            self.BIG_Y = top + self.BIG_R
        s = self.BIG_R / self.rb
        self.full = (s, (self.BIG_Y - H / 2) / s)
        rs_px = self.rs * s
        gap = 90 if q > 0.2 else max(80, rs_px + 60)
        self.small_y = -(self.rb + gap / s + self.rs)   # küçük cisim büyüğün altında
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
            st.update(fill=1.0, fill_a=(1 - span(u, 0, 0.25)) if self.show_fill else 0.0,
                      row=span(u, 0.15, 0.8), row_a=1.0,
                      counter=("across", span(u, 0.15, 0.8)))
        elif step == "outro":
            st.update(cam=self.zoomed(lerp(1.0, 1.15, ease(u))), row=1.0,
                      row_a=(1 - span(u, 0.5, 1)) if self.show_row else 0.0)
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
        small_rings = BODIES[self.small].get("rings")   # küçük cisim Satürn ise halkasıyla tanınsın
        if small_rings:
            put(ring_patch(sx, syy, sr, front=False))
        put(sphere_patch(self.small, sx, syy, sr, 0.0))
        if small_rings:
            put(ring_patch(sx, syy, sr, front=True))
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
    """Okunur uzunluk: 0.2 mm, 1.8 cm, 5.6 m, 5.9 km, 1,391,400 km, 24 million km, 38 billion km."""
    if km >= 1e9:
        return f"{km / 1e9:.0f} billion km"
    if km >= 1e7:
        return f"{km / 1e6:.0f} million km"
    if km >= 100:
        return f"{km:,.0f} km"
    if km >= 1:
        return f"{km:.1f} km"
    if km >= 0.001:
        return f"{km * 1000:.1f} m"
    if km >= 1e-5:
        return f"{km * 1e5:.1f} cm"
    return f"{km * 1e6:.1f} mm"


class Collapse:
    """Bir gök cismi (ör. Güneş) aynı kütleyle olay ufku boyutuna sıkışır. Kamera cisimle birlikte yaklaşır
    (ekranda 'ZOOM x' yazar). Sonunda kara delik ve gerçek ölçekte bir karşılaştırma (ör. Everest'in boyu).
    Adımlar: intro, size, crush, horizon, compare, outro"""

    CY = 1040
    CMP_DROP = 260       # karşılaştırma sahnesinde her şey bu kadar aşağı iner (altyazı şeridi ~880-1030 px)

    def __init__(self, spec):
        self.body = spec.get("body", "sun")
        self.d0 = BODIES[self.body]["d"]
        self.mass = float(spec.get("mass_suns", 1.0))
        self.dh = horizon_km(self.mass)
        cmp_ = spec.get("compare") or {}
        self.cmp_name, self.cmp_km = cmp_.get("name"), cmp_.get("km")
        self.cmp_shape = cmp_.get("shape", "mountain")   # "mountain" (yükseklik), "coin" (çap) ya da "ruler" (uzunluk)

    def state(self, step, u):
        st = {"d": self.d0, "rpx": 380.0, "zoom": 1.0, "ruler": 0.0, "hole": 0.0, "flash": 0.0, "cmp": 0.0,
              "drop": 0.0, "label": 1.0, "spin": u}
        if step == "intro":
            # kanca karesi: yıldız kendi ışığıyla parlak; gezegenin gece yarısı karanlık, o yüzden daha yakından başlar
            r0 = 400 if BODIES[self.body].get("star") else 520
            # ad etiketi yok: kanca başlığı ekranda ve cismin adını zaten söylüyor (üst üste binmesin)
            st.update(rpx=lerp(r0, 380, ease(u)), label=0.0)
        elif step == "size":
            st.update(ruler=ease(span(u, 0.05, 0.5)), label=span(u, 0.0, 0.3))
        elif step == "crush":
            t = ease(span(u, 0.05, 0.92))
            d = math.exp(lerp(math.log(self.d0), math.log(self.dh), t))
            rpx = lerp(380, 160, t)
            st.update(d=d, rpx=rpx, zoom=(self.d0 / d) * (rpx / 380), ruler=1.0, label=1 - span(u, 0, 0.08))
        elif step == "horizon":
            st.update(d=self.dh, rpx=160, zoom=(self.d0 / self.dh) * 160 / 380, ruler=1.0,
                      hole=span(u, 0.0, 0.25), flash=max(0.0, 1 - u * 4), label=0.0)
        elif step == "compare":
            t = ease(span(u, 0.0, 0.5))
            # önce kara delik yerine (sola, altyazının altına) iner, karşılaştırma ancak orada belirir
            st.update(d=self.dh, rpx=lerp(160, 110, t), zoom=(self.d0 / self.dh) * lerp(160, 110, t) / 380,
                      ruler=1 - span(u, 0.0, 0.12), hole=1.0, cmp=ease(span(u, 0.3, 0.6)),
                      drop=ease(span(u, 0.0, 0.4)), label=0.0)
        elif step == "outro":
            st.update(d=self.dh, rpx=lerp(110, 150, ease(u)), zoom=(self.d0 / self.dh) * lerp(110, 150, ease(u)) / 380,
                      hole=1.0, cmp=1 - span(u, 0.0, 0.2), drop=1 - ease(span(u, 0.2, 0.65)), label=0.0)
        else:
            raise ValueError(f"collapse şablonunda '{step}' adımı yok: {', '.join(TEMPLATES['collapse'])}")
        return st

    def frame(self, step, u, words=None):
        st = self.state(step, u)
        img = backdrop(st["zoom"] ** 0.5)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        # karşılaştırmada kara delik sola ve altyazı şeridinin altına kayar (karşılaştırma altyazının altında kalmasın)
        cx, cy, r = W / 2 - st["drop"] * W * 0.20, self.CY + self.CMP_DROP * st["drop"], st["rpx"]
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
            if self.cmp_shape == "coin":
                # madeni para: kara deliğin yanında aynı ölçekte gümüş bir disk (çap gerçek oran)
                ccy = base - hpx / 2
                d.ellipse((bx - hpx / 2, ccy - hpx / 2, bx + hpx / 2, ccy + hpx / 2), fill=(176, 182, 196, a))
                d.ellipse((bx - hpx * 0.42, ccy - hpx * 0.42, bx + hpx * 0.42, ccy + hpx * 0.42),
                          outline=(225, 230, 240, a), width=max(2, int(hpx * 0.03)))
                d.ellipse((bx - hpx * 0.30, ccy - hpx * 0.36, bx - hpx * 0.02, ccy - hpx * 0.10),
                          fill=(215, 220, 232, int(a * 0.6)))
            elif self.cmp_shape == "ruler":
                # okul cetveli: dik duran ince tahta şerit (uzunluk gerçek oran), her cm'de çentik, 5 cm'de uzun çentik
                rw = max(14.0, hpx * 0.12)
                d.rectangle((bx - rw / 2, base - hpx, bx + rw / 2, base), fill=(214, 178, 112, a))
                d.rectangle((bx - rw / 2, base - hpx, bx + rw / 2, base), outline=(150, 112, 58, a), width=2)
                n = int(round(self.cmp_km * 1e5))   # santimetre
                for k in range(1, n):
                    y = base - hpx * k / n
                    ln = rw * (0.55 if k % 5 == 0 else 0.3)
                    d.line((bx - rw / 2, y, bx - rw / 2 + ln, y), fill=(70, 50, 25, a), width=2)
            else:
                pts = [(bx - hpx * 0.55, base), (bx - hpx * 0.14, base - hpx * 0.72), (bx, base - hpx),
                       (bx + hpx * 0.17, base - hpx * 0.7), (bx + hpx * 0.58, base)]
                d.polygon(pts, fill=(120, 128, 150, a))
                d.polygon([(bx - hpx * 0.14, base - hpx * 0.78), (bx, base - hpx), (bx + hpx * 0.17, base - hpx * 0.76),
                           (bx, base - hpx * 0.84)], fill=(240, 244, 255, a))
            ruler(layer, bx + hpx * 0.68, base, base - hpx, GREEN, st["cmp"])
            pill(layer, (bx, base - hpx - 70), km_label(self.cmp_km), 40, GREEN, anchor="mm", alpha=st["cmp"])
            text(layer, (bx, base + 50), self.cmp_name.upper(), 34, fill=(255, 255, 255, a))
            ruler(layer, cx - r - 40, cy + r, cy - r, YELLOW, st["cmp"])
            pill(layer, (cx, cy - r - 70), f"{km_label(self.dh)} wide", 40, YELLOW, alpha=st["cmp"])
            text(layer, (cx, cy + r + 50), "BLACK HOLE", 34, fill=(255, 255, 255, a))
        if st["ruler"] > 0:
            g = st["ruler"]
            d = ImageDraw.Draw(layer)
            yb = cy + r + 70
            c = (*YELLOW, int(255 * g))
            d.line((cx - r, yb, cx + r, yb), fill=c, width=6)
            for x in (cx - r, cx + r):
                d.line((x, yb - 22, x, yb + 22), fill=c, width=6)
            pill(layer, (cx, yb + 70), f"{km_label(st['d'])} wide", 46, YELLOW, alpha=g)
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
                      pill=(f"{km_label(2 * self.rs_small)} wide", span(u, 0.3, 0.6), self.small["name"]))
        elif step == "orbit":
            st.update(s=self.s_inner, small=1.0, big=0.0, pulse=min(1.0, u * 4),
                      pill=(f"{km_label(2 * self.rs_small)} wide", 1.0, self.small["name"]))
        elif step == "zoom":
            t = ease(span(u, 0.0, 0.9))
            st.update(s=math.exp(lerp(math.log(self.s_inner), math.log(self.s_nep), t)), small=1 - span(u, 0.6, 0.9), big=0.0)
        elif step == "grow":
            t = ease(span(u, 0.0, 0.85))
            st.update(s=math.exp(lerp(math.log(self.s_nep), math.log(self.s_big), t)), big=t,
                      pill=(f"{km_label(2 * self.rs_big)} wide", span(u, 0.5, 0.8), self.big["name"]))
        elif step == "outro":
            st.update(s=self.s_big * lerp(1.0, 1.08, ease(u)),
                      pill=(f"{km_label(2 * self.rs_big)} wide", 1 - span(u, 0, 0.4), self.big["name"]))
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



# ---------------------------------------------------------------- gökyüzü, harita ve "içine sığar" şablonları

def hruler(layer, xa, xb, y, color, alpha=1.0, cap=22):
    """Yatay ölçü çizgisi; uçlarında dikey çentik."""
    if alpha <= 0:
        return
    d = ImageDraw.Draw(layer)
    c = (*color, int(255 * alpha))
    xa, xb = sorted((xa, xb))
    d.line((max(xa, -10), y, min(xb, W + 10), y), fill=c, width=6)
    for x in (xa, xb):
        if -10 < x < W + 10:
            d.line((x, y - cap, x, y + cap), fill=c, width=6)


def angular_deg(radius_km, distance_km):
    """Bir cismin gökyüzünde kapladığı açı (derece, çap)."""
    return 2 * math.degrees(math.atan(radius_km / distance_km))


def deg_label(deg):
    """Okunur açı: 0.5°, 1.9°, 21°."""
    return f"{deg:.1f}°" if deg < 10 else f"{deg:.0f}°"


@functools.lru_cache(maxsize=4)
def _sky_backdrop(ground):
    """Gökyüzü: şehirde alacakaranlık (lacivertten ufukta mora), Ay'da ve Merkür'de havasız kara gök; yıldızlar."""
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    if ground == "city":
        top, mid, low = np.array([4, 6, 22]), np.array([14, 22, 58]), np.array([58, 44, 88])
        t = np.clip(yy / 0.82, 0, 1)
        col = np.where(t < 0.6, top + (mid - top) * (t / 0.6), mid + (low - mid) * ((t - 0.6) / 0.4))
        star_a = 0.55
    else:
        col = np.broadcast_to(np.array([3, 3, 8], np.float32), (H, 3))
        star_a = 1.0
    img = np.broadcast_to(col[:, None, :], (H, W, 3)).astype(np.float32)
    rng = np.random.default_rng(7)
    for _ in range(900):
        x, y = int(rng.integers(0, W)), int(rng.integers(0, int(H * 0.8)))
        b = min(1.0, 0.25 + rng.pareto(3.0) * 0.35) * star_a * (1 - y / (H * 0.95))
        img[y, x] = np.maximum(img[y, x], 255 * b)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).convert("RGBA")


@functools.lru_cache(maxsize=4)
def _ground_layer(ground):
    """Ön plan: kodla çizilmiş şehir silueti (ışıklı pencereler) ya da kraterli, kayalık bir ufuk."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    rng = np.random.default_rng(11)
    if ground == "city":
        x = -20
        while x < W + 20:
            bw = int(rng.integers(50, 150))
            bh = int(rng.integers(140, 430)) if rng.random() > 0.2 else int(rng.integers(430, 560))
            top = H - bh
            d.rectangle((x, top, x + bw, H), fill=(8, 10, 18, 255))
            if rng.random() < 0.3:      # anten
                ax = x + bw // 2
                d.line((ax, top, ax, top - int(rng.integers(30, 90))), fill=(8, 10, 18, 255), width=4)
            for wy in range(top + 18, H - 10, 26):
                for wx in range(x + 10, x + bw - 14, 22):
                    if rng.random() < 0.32:
                        a = int(rng.integers(120, 230))
                        d.rectangle((wx, wy, wx + 9, wy + 12), fill=(255, 214, 140, a))
            x += bw + int(rng.integers(0, 14))
        return layer
    base = (92, 90, 88) if ground == "moon" else (86, 76, 66)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    horizon = H * 0.80 + 26 * ((xx - W / 2) / (W / 2)) ** 2          # hafif eğri ufuk
    noise = np.asarray(Image.fromarray((fbm(5, base=6, octaves=5, h=H // 4, w=W // 4) * 255).astype(np.uint8))
                       .resize((W, H), Image.BILINEAR), np.float32) / 255
    depth = np.clip((yy - horizon) / (H - horizon.min()), 0, 1)
    shade = (0.55 + 0.5 * noise) * (0.55 + 0.45 * depth)
    rgb = np.clip(np.array(base, np.float32) * shade[..., None], 0, 255)
    alpha = np.clip((yy - horizon) * 0.8, 0, 1) * 255
    img = Image.fromarray(np.dstack([rgb, alpha]).astype(np.uint8), "RGBA")
    d = ImageDraw.Draw(img)
    for _ in range(26):               # kraterler: perspektifte yassı elipsler
        cx = float(rng.uniform(0, W))
        cy = float(rng.uniform(H * 0.83, H * 1.0))
        r = float(rng.uniform(14, 70)) * (0.5 + (cy - H * 0.8) / (H * 0.2))
        d.ellipse((cx - r, cy - r * 0.32, cx + r, cy + r * 0.32), fill=(40, 38, 36, 150))
        d.arc((cx - r, cy - r * 0.32, cx + r, cy + r * 0.32), 200, 340, fill=(150, 146, 140, 160), width=3)
    return img


class Sky:
    """Bir gök cismi gökyüzünde gerçek açısal boyutuyla: ör. Ay'ın yerinde Jüpiter, Ay'dan görünen Dünya.
    spec: body, distance_km, ground ("city" | "moon" | "mercury"), fov (ekranın dikey görüş açısı, derece),
    compare: {body, distance_km, name} (bugün gördüğümüz, kıyas için). Adımlar: intro, compare, grow, measure, outro"""

    CX, CY = W / 2, 760

    def __init__(self, spec):
        self.body = spec["body"]
        self.dist = float(spec["distance_km"])
        self.ground = spec.get("ground", "city")
        self.fov = float(spec.get("fov", 40))
        cmp_ = spec.get("compare") or {}
        self.cbody = cmp_.get("body", "moon")
        self.cdist = float(cmp_.get("distance_km", 384400))
        self.cname = cmp_.get("name", BODIES[self.cbody]["name"])
        self.than = cmp_.get("than", self.cname)          # "41x wider than <than>"
        self.deg = angular_deg(BODIES[self.body]["d"] / 2, self.dist)
        # halkalı gezegende ölçü halkaların dış kenarından (A halkası) alınabilir: "measure": "rings"
        self.k_meas = RING_BANDS[-1][1] if spec.get("measure") == "rings" and BODIES[self.body].get("rings") else 1.0
        self.deg_m = angular_deg(BODIES[self.body]["d"] / 2 * self.k_meas, self.dist)
        self.cdeg = angular_deg(BODIES[self.cbody]["d"] / 2, self.cdist)
        self.ratio = self.deg_m / self.cdeg
        self.ppd = H / self.fov
        self.r = self.deg / 2 * self.ppd
        self.cr = self.cdeg / 2 * self.ppd
        self.cy = spec.get("cy", self.CY)

    def state(self, step, u):
        st = {"zoom": 1.0, "main": 1.0, "cmp": 0.0, "grow": 1.0, "ruler": 0.0, "label": 0.0, "pulse": 0.0}
        if step == "intro":
            st.update(zoom=lerp(1.04, 1.0, ease(u)))
        elif step == "compare":
            t = ease(span(u, 0.0, 0.35))
            st.update(main=1 - t, cmp=t, pulse=span(u, 0.3, 0.5), label=span(u, 0.35, 0.55))
        elif step == "grow":
            t = ease(span(u, 0.08, 0.85))
            st.update(main=t, cmp=1 - t, grow=t, label=1 - span(u, 0.0, 0.12))
        elif step == "measure":
            st.update(ruler=ease(span(u, 0.05, 0.45)))
        elif step == "outro":
            st.update(zoom=lerp(1.0, 1.06, ease(u)), ruler=1 - span(u, 0.0, 0.3))
        else:
            raise ValueError(f"sky şablonunda '{step}' adımı yok: {', '.join(TEMPLATES['sky'])}")
        return st

    def _body(self, img, body, r, alpha, rot=0.25):
        if alpha <= 0.01 or r < 0.6:
            return
        rings = BODIES[body].get("rings")
        parts = []
        if rings:
            parts.append(ring_patch(self.CX, self.cy, r, front=False))
        parts.append(sphere_patch(body, self.CX, self.cy, r, rot))
        if rings:
            parts.append(ring_patch(self.CX, self.cy, r, front=True))
        for p in parts:
            if not p:
                continue
            patch = p[0]
            if alpha < 0.999:
                patch = patch.copy()
                patch.putalpha(patch.getchannel("A").point(lambda v: int(v * alpha)))
            img.alpha_composite(patch, (p[1], p[2]))

    def frame(self, step, u, words=None):
        st = self.state(step, u)
        img = _sky_backdrop(self.ground).copy()
        z = st["zoom"]
        r_main = self.r * z if step != "grow" else math.exp(lerp(math.log(max(self.cr, 0.6)), math.log(self.r), st["grow"]))
        self._body(img, self.body, r_main, st["main"])
        self._body(img, self.cbody, self.cr * z, st["cmp"], rot=0.0)
        img.alpha_composite(_ground_layer(self.ground))
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if st["pulse"] > 0:
            ring_pulse(layer, self.CX, self.cy, max(self.cr, 6), u, YELLOW, st["pulse"] * st["cmp"])
        if st["label"] > 0.01:
            a = st["label"]
            # kıyas cisminin adı ve açısı cismin ÜSTÜNDE: ortadaki altyazı şeridine (CAPTION_BAND) binmesin
            yl = min(self.cy - max(self.cr, 6) - 210, CAPTION_BAND[0] - 120)
            text(layer, (self.CX, yl), self.cname.upper(), 44, fill=(255, 255, 255, int(255 * a)))
            pill(layer, (self.CX, yl + 70), deg_label(self.cdeg), 44, YELLOW, alpha=a)
        if step in ("grow", "measure", "outro"):
            deg = self.deg_m if step != "grow" else self.deg_m * r_main / self.r
            a = span(u, 0.5, 0.8) if step == "grow" else (1.0 if step == "measure" else 1 - span(u, 0, 0.3))
            if a > 0.01:
                yb = min(self.cy + r_main + 70, H * 0.74)
                pill(layer, (self.CX, yb), f"{deg_label(deg)} OF SKY", 46, YELLOW, alpha=a)
        if st["ruler"] > 0:
            g = st["ruler"]
            half = min(self.r * self.k_meas, W / 2 - 30) * ease(g)
            yr = self.cy - min(self.r, 600) - 40 if self.cy - self.r - 40 > 180 else self.cy
            hruler(layer, self.CX - half, self.CX + half, yr, YELLOW, g)
            text(layer, (self.CX, min(self.cy + self.r + 150, H * 0.79)),
                 f"{self.ratio:.0f}x WIDER THAN {self.than.upper()}", 40,
                 fill=(255, 255, 255, int(255 * g)))
        img = glow(img)
        img.paste(layer, (0, 0), layer)
        return img


class Engulf:
    """Küçük bir yörünge (ör. Ay'ın Dünya etrafındaki yörüngesi) büyük bir cismin (Güneş) içine gerçek ölçekte sığar.
    spec: center (earth), orbiter (moon), orbit_km (384400), big (sun). Adımlar: intro, orbit, engulf, measure, outro"""

    CY = 1000
    ORBIT_PX = 250

    def __init__(self, spec):
        self.center = spec.get("center", "earth")
        self.orbiter = spec.get("orbiter", "moon")
        self.big = spec.get("big", "sun")
        self.orbit = float(spec.get("orbit_km", 384400))
        self.rc = BODIES[self.center]["d"] / 2
        self.rb = BODIES[self.big]["d"] / 2
        self.s_full = self.ORBIT_PX / self.orbit
        self.s_close = 300 / self.rc

    def state(self, step, u):
        st = {"s": self.s_full, "orbit": 1.0, "big": 0.0, "ruler_o": 0.0, "ruler_b": 0.0, "labels": 1.0, "ang": u * 0.6}
        if step == "intro":
            t = ease(span(u, 0.0, 0.8))
            st.update(s=math.exp(lerp(math.log(self.s_close), math.log(self.s_full), t)), orbit=span(u, 0.5, 0.9),
                      labels=span(u, 0.7, 1.0))
        elif step == "orbit":
            st.update(ruler_o=ease(span(u, 0.1, 0.6)))
        elif step == "engulf":
            st.update(big=ease(span(u, 0.05, 0.7)), ruler_o=1.0)
        elif step == "measure":
            st.update(big=1.0, ruler_o=1.0, ruler_b=ease(span(u, 0.05, 0.5)))
        elif step == "outro":
            st.update(big=1.0, s=self.s_full * lerp(1.0, 1.08, ease(u)), ruler_o=1 - span(u, 0, 0.3),
                      ruler_b=1 - span(u, 0, 0.3))
        else:
            raise ValueError(f"engulf şablonunda '{step}' adımı yok: {', '.join(TEMPLATES['engulf'])}")
        return st

    def frame(self, step, u, words=None):
        st = self.state(step, u)
        s = st["s"]
        img = backdrop(s / self.s_full)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        cx, cy = W / 2, self.CY
        if st["big"] > 0:
            rb = self.rb * s * st["big"]
            p = sphere_patch(self.big, cx, cy, rb, 0.1)
            if p:
                img.alpha_composite(p[0], (p[1], p[2]))
        ro = self.orbit * s
        over = st["big"] > 0.3
        if st["orbit"] > 0 and ro < 3000:
            col = (20, 16, 30) if over else (200, 215, 255)
            a = int(230 * st["orbit"])
            dl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dd = ImageDraw.Draw(dl)
            n = 90
            for k in range(0, n, 2):        # kesik çizgili yörünge
                a0, a1 = 2 * math.pi * k / n, 2 * math.pi * (k + 1) / n
                dd.line((cx + ro * math.cos(a0), cy + ro * math.sin(a0), cx + ro * math.cos(a1), cy + ro * math.sin(a1)),
                        fill=(*col, a), width=5)
            img.alpha_composite(dl)
        # merkez cismi ve yörüngedeki uydu (gerçek boyut; çok küçükse yine de görünür bir nokta)
        rc = max(self.rc * s, 3.5)
        p = sphere_patch(self.center, cx, cy, rc, 0.2) if self.rc * s > 3.5 else None
        if p:
            img.alpha_composite(p[0], (p[1], p[2]))
        else:
            d.ellipse((cx - rc, cy - rc, cx + rc, cy + rc), fill=(90, 160, 255, 255))
        ang = -math.pi / 4 + st["ang"]
        mx, my = cx + ro * math.cos(ang), cy + ro * math.sin(ang)
        if st["orbit"] > 0.5 and ro < 3000:
            rm = max(BODIES[self.orbiter]["d"] / 2 * s, 3)
            d.ellipse((mx - rm, my - rm, mx + rm, my + rm), fill=(225, 225, 220, int(255 * st["orbit"])))
        if st["labels"] > 0.01 and ro < 1000:
            a = int(255 * st["labels"])
            text(layer, (cx, cy + 36), BODIES[self.center]["name"].upper(), 32, fill=(255, 255, 255, a))
            text(layer, (mx + 18, my - 18), BODIES[self.orbiter]["name"].upper(), 32, fill=(255, 255, 255, a), anchor="lb")
        if st["ruler_o"] > 0:
            g = st["ruler_o"]
            y = cy - ro - 70
            hruler(layer, cx - ro, cx + ro, y, GREEN if not over else (255, 255, 255), g)
            pill(layer, (cx, y - 60), f"{round(2 * self.orbit):,} km", 40, GREEN, alpha=g)
        if st["ruler_b"] > 0:
            g = st["ruler_b"]
            rb = self.rb * s
            y = cy + rb + 60
            hruler(layer, cx - rb, cx + rb, y, YELLOW, g)
            pill(layer, (cx, y + 64), f"{BODIES[self.big]['name'].upper()}: {round(2 * self.rb):,} km", 40, YELLOW, alpha=g)
        img = glow(img)
        img.paste(layer, (0, 0), layer)
        return img


@functools.lru_cache(maxsize=1)
def countries():
    """Ülke ana hatları (Natural Earth, kamu malı), km cinsinden, kuzey yukarı: geo/countries.json"""
    import json
    return json.loads((Path(__file__).resolve().parent / "geo" / "countries.json").read_text(encoding="utf-8"))["countries"]


def _enclosing(pts):
    """Noktaları içine alan en küçük çemberin yaklaşık merkezi ve yarıçapı."""
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    for i in range(1, 4000):
        fx, fy = max(pts, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
        cx += (fx - cx) / (i + 1)
        cy += (fy - cy) / (i + 1)
    return cx, cy, max(math.hypot(p[0] - cx, p[1] - cy) for p in pts)


class MapOver:
    """Bir ülkenin ana hatları bir gök cisminin (Ay) üstüne aynı ölçekte konur: ülke cisimden geniş mi, sığıyor mu?
    spec: country (geo/countries.json anahtarı), body (moon), measure ("width" | "height").
    Adımlar: intro, place, measure, verdict, outro"""

    CY = 1000
    R_PX = 400

    def __init__(self, spec):
        self.key = spec["country"]
        c = countries()[self.key]
        self.name = c["name"]
        self.body = spec.get("body", "moon")
        self.rb = BODIES[self.body]["d"] / 2
        pts = [p for ring in c["rings"] for p in ring]
        ex, ey, er = _enclosing(pts)
        self.rings = [[(x - ex, y - ey) for x, y in ring] for ring in c["rings"]]
        self.measure = spec.get("measure", "width")
        self.span_km = c["width_km"] if self.measure == "width" else c["height_km"]
        self.fits = er <= self.rb
        rpx = self.R_PX
        if not self.fits:          # geniş ülke: ekrana sığsın diye Ay biraz küçülür
            rpx = min(rpx, (W - 60) / 2 / (c["width_km"] / 2 / self.rb), 900 / (c["height_km"] / 2 / self.rb))
        self.s = rpx / self.rb
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.box = (min(xs) - ex, max(xs) - ex, min(ys) - ey, max(ys) - ey)

    def state(self, step, u):
        st = {"zoom": 1.0, "shape": 1.0, "drop": 0.0, "ruler": 0.0, "verdict": 0.0}
        if step == "intro":
            st.update(zoom=lerp(1.12, 1.0, ease(u)), shape=0.0)
        elif step == "place":
            t = ease(span(u, 0.05, 0.6))
            st.update(shape=t, drop=1 - t)
        elif step == "measure":
            st.update(ruler=ease(span(u, 0.05, 0.5)))
        elif step == "verdict":
            st.update(ruler=1.0, verdict=ease(span(u, 0.0, 0.4)))
        elif step == "outro":
            st.update(zoom=lerp(1.0, 1.05, ease(u)), ruler=1 - span(u, 0, 0.3), verdict=1 - span(u, 0.3, 0.8))
        else:
            raise ValueError(f"map şablonunda '{step}' adımı yok: {', '.join(TEMPLATES['map'])}")
        return st

    def frame(self, step, u, words=None):
        st = self.state(step, u)
        img = backdrop(st["zoom"])
        cx, cy = W / 2, self.CY
        s = self.s * st["zoom"]
        p = sphere_patch(self.body, cx, cy, self.rb * s, 0.0)
        if p:
            img.alpha_composite(p[0], (p[1], p[2]))
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if st["shape"] > 0.01:
            k = s * (1 + 0.35 * st["drop"])
            oy = -260 * st["drop"]
            polys = [[(cx + x * k, cy + oy - y * k) for x, y in ring] for ring in self.rings]
            a = st["shape"]
            fill_l = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            fd = ImageDraw.Draw(fill_l)
            for poly in polys:
                fd.polygon(poly, fill=(70, 190, 255, int(120 * a)))
            if st["verdict"] > 0:
                mask = Image.new("L", (W, H), 0)
                md = ImageDraw.Draw(mask)
                for poly in polys:
                    md.polygon(poly, fill=255)
                disk = Image.new("L", (W, H), 0)
                rr = self.rb * s
                ImageDraw.Draw(disk).ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=255)
                if self.fits:
                    tint = Image.new("RGBA", (W, H), (*GREEN, int(110 * st["verdict"])))
                    fill_l.paste(tint, (0, 0), mask)
                else:
                    out = ImageChops.subtract(mask, disk)
                    tint = Image.new("RGBA", (W, H), (*RED, int(170 * st["verdict"])))
                    fill_l.paste(tint, (0, 0), out)
            img.alpha_composite(fill_l)
            od = ImageDraw.Draw(layer)
            for poly in polys:
                od.line(poly + [poly[0]], fill=(255, 255, 255, int(235 * a)), width=4, joint="curve")
            text(layer, (cx, cy + oy), self.name.upper(), 44, fill=(255, 255, 255, int(255 * a)))
        if st["ruler"] > 0:
            g = st["ruler"]
            rr = self.rb * s
            ytop = cy - max(rr, self.box[3] * s) - 70
            hruler(layer, cx - rr, cx + rr, ytop, YELLOW, g)
            pill(layer, (cx, ytop - 62), f"{BODIES[self.body]['name'].upper()}: {round(2 * self.rb):,} km", 40, YELLOW, alpha=g)
            col = GREEN if self.fits else RED
            if self.measure == "width":
                ybot = cy + max(rr, -self.box[2] * s) + 70
                hruler(layer, cx + self.box[0] * s, cx + self.box[1] * s, ybot, col, g)
                pill(layer, (cx, ybot + 64), f"{self.name.upper()}: {self.span_km:,} km", 40, col, alpha=g)
            else:
                xr = cx + max(rr, self.box[1] * s) + 40
                xr = min(xr, W - 40)
                ruler(layer, xr, cy - self.box[3] * s, cy - self.box[2] * s, col, g)
                ybot = cy + rr + 80
                pill(layer, (cx, ybot), f"{self.name.upper()}: {self.span_km:,} km TALL", 40, col, alpha=g)
        if st["verdict"] > 0.01:
            a = st["verdict"]
            msg = "FITS ON THE " + BODIES[self.body]["name"].upper() if self.fits else \
                "WIDER THAN THE " + BODIES[self.body]["name"].upper()
            pill(layer, (W / 2, 330), msg, 52, GREEN if self.fits else RED, alpha=a)
        img = glow(img)
        img.paste(layer, (0, 0), layer)
        return img


SIMS = {"lineup": Lineup, "compare": Compare, "collapse": Collapse, "giant": Giant,
        "sky": Sky, "engulf": Engulf, "map": MapOver}


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
        if (spec.get("compare") or {}).get("shape", "mountain") not in ("mountain", "coin", "ruler"):
            errs.append("sim.compare.shape \"mountain\", \"coin\" ya da \"ruler\" olmalı")
    if kind == "giant":
        holes = spec.get("holes") or []
        if len(holes) < 2 or any(not isinstance(h.get("mass_suns"), (int, float)) or not h.get("name") for h in holes):
            errs.append("sim.holes en az iki kara delik olmalı: [{name, mass_suns}, ...]")
        if spec.get("fit_orbit", "Mercury") not in dict(ORBITS):
            errs.append(f"sim.fit_orbit bir gezegen adı olmalı ({', '.join(n for n, _ in ORBITS)})")
    if kind == "sky":
        for b in [spec.get("body")] + [(spec.get("compare") or {}).get("body", "moon")]:
            if b not in BODIES:
                errs.append(f"sim.body '{b}' tanımlı bir gök cismi değil ({', '.join(BODIES)})")
        if not isinstance(spec.get("distance_km"), (int, float)) or spec.get("distance_km", 0) <= 0:
            errs.append("sim.distance_km pozitif bir sayı olmalı")
        if spec.get("ground", "city") not in ("city", "moon", "mercury"):
            errs.append("sim.ground \"city\", \"moon\" ya da \"mercury\" olmalı")
    if kind == "engulf":
        for k in ("center", "orbiter", "big"):
            if spec.get(k, {"center": "earth", "orbiter": "moon", "big": "sun"}[k]) not in BODIES:
                errs.append(f"sim.{k} tanımlı bir gök cismi değil")
    if kind == "map":
        if spec.get("country") not in countries():
            errs.append(f"sim.country geo/countries.json içinde yok ({', '.join(countries())})")
        if spec.get("measure", "width") not in ("width", "height"):
            errs.append("sim.measure \"width\" ya da \"height\" olmalı")
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
