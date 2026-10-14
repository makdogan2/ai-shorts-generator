"""
Kodla üretilen özgün arka plan müziği. Hiçbir kayıt ya da örnek kullanılmaz: her nota sinüs dalgalarından
hesaplanır. Bu yüzden telif ya da Content ID iddiası doğmaz. Her video kendi tohumuyla (slug) farklı bir
parça alır: ton, akor yürüyüşü, tempo ve arpej deseni değişir.

Katmanlar: yumuşak pad (minör akorlar, yavaş açılıp kapanan), alt bas, yankılı arpej ve hafif vuruş.
Kullanım: make_track("out.wav", seconds=30, seed="video-slug")
"""
import math
import random
import wave
import zlib
from pathlib import Path

import numpy as np

SR = 44100

# akor yürüyüşleri: tonun köküne göre yarım ses aralıkları (minör tonda i, VI, III, VII ...)
I, IV, V, VI, III, VII = [0, 3, 7], [5, 8, 12], [7, 11, 14], [-4, 0, 3], [3, 7, 10], [-2, 2, 5]
PROGRESSIONS = [
    [I, VI, III, VII],
    [I, VII, VI, VII],
    [I, IV, VI, V],
    [VI, VII, I, I],
    [I, VI, IV, V],
]
ARP_PATTERNS = [[0, 1, 2, 1], [0, 2, 1, 2], [0, 1, 2, 3], [2, 1, 0, 1]]


def _hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _soft_saw(f, t, harmonics=10, cutoff=2600.0):
    """Yumuşatılmış testere dalgası: üst harmonikler kısılır, sert değil sıcak bir ton çıkar."""
    out = np.zeros_like(t)
    for k in range(1, harmonics + 1):
        if f * k > 9000:
            break
        w = 1.0 / (1.0 + (f * k / cutoff) ** 2)
        out += w * np.sin(2 * math.pi * f * k * t) / k
    return out


def _env(n, attack, release):
    """Yavaş açılıp kapanan genlik zarfı (örnek sayısı cinsinden)."""
    e = np.ones(n)
    a, r = min(int(attack * SR), n // 2), min(int(release * SR), n // 2)
    if a:
        e[:a] = np.sin(np.linspace(0, math.pi / 2, a)) ** 2
    if r:
        e[-r:] *= np.cos(np.linspace(0, math.pi / 2, r)) ** 2
    return e


def _reverb(x, seconds=2.4, seed=0):
    """Yapay yankı: sönümlenen gürültüyle FFT evrişimi (iki kanal için ayrı gürültü, geniş ses)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros_like(x)
    for ch in range(2):
        ir = rng.standard_normal(n) * np.exp(-t / (seconds / 4.5))
        ir = np.convolve(ir, np.ones(24) / 24, mode="same")      # yüksek frekansları yumuşat
        ir[:int(0.012 * SR)] = 0                                   # kısa ön gecikme
        ir /= np.sqrt((ir ** 2).sum())
        size = 1 << int(math.ceil(math.log2(len(x) + n)))
        y = np.fft.irfft(np.fft.rfft(x[:, ch], size) * np.fft.rfft(ir, size), size)[:len(x)]
        out[:, ch] = y
    return out


def make_track(path, seconds=30.0, seed="astro"):
    rng = random.Random(str(seed))
    root = rng.choice([52, 53, 55, 57, 58, 60])          # E3 .. C4 arası bir minör ton
    prog = rng.choice(PROGRESSIONS)
    bpm = rng.choice([88, 92, 96, 100, 104])
    pattern = rng.choice(ARP_PATTERNS)
    beat = 60.0 / bpm
    chord_len = 8 * beat                                  # her akor 2 ölçü
    n = int((seconds + 3) * SR)
    t_all = np.arange(n) / SR
    mix_pad = np.zeros((n, 2))
    mix_arp = np.zeros((n, 2))
    mix_dry = np.zeros((n, 2))

    k = 0
    start = 0.0
    while start < seconds + 1:
        chord = prog[k % len(prog)]
        s0 = int(start * SR)
        seg = int((chord_len + 1.5) * SR)                # bir sonraki akora 1,5 sn taşar: yumuşak geçiş
        s1 = min(n, s0 + seg)
        t = t_all[:s1 - s0]
        env = _env(s1 - s0, 1.4, 1.8)
        # pad: akor sesleri + bir oktav aşağıda kök, her ses üç hafif farklı akortla (kalınlık)
        notes = [root + c for c in chord] + [root + chord[0] - 12]
        for vi, m in enumerate(notes):
            level = 0.025 if vi == len(notes) - 1 else 0.05      # alttaki kök ses daha kısık: çamurlaşmasın
            for d, pan in ((-0.07, 0.25), (0.0, 0.5), (0.07, 0.75)):
                f = _hz(m + d)
                v = _soft_saw(f, t) * env * level
                mix_pad[s0:s1, 0] += v * (1 - pan)
                mix_pad[s0:s1, 1] += v * pan
        # alt bas: kökün iki oktav altı, sinüs + az ikinci harmonik
        fb = _hz(root + chord[0] - 12)                    # telefon hoparlöründe de duyulan bas (çok derin değil)
        bass = (np.sin(2 * math.pi * fb * t) + 0.25 * np.sin(4 * math.pi * fb * t)) * _env(s1 - s0, 0.4, 1.2) * 0.07
        # parıltı: akorun iki oktav üstünde çok yumuşak sinüsler (müziğe hava katar)
        for m in chord:
            f = _hz(root + 24 + m)
            sh = np.sin(2 * math.pi * f * t + m) * (0.6 + 0.4 * np.sin(2 * math.pi * 0.25 * t + m)) * env * 0.012
            mix_pad[s0:s1] += sh[:, None]
        mix_dry[s0:s1] += bass[:, None]
        # arpej: akor sesleri bir oktav yukarıda, sekizlik notalar, çalınıp sönen ses
        tones = sorted(root + 24 + c for c in chord) + [root + 36 + chord[0]]
        step = beat / 2
        for j in range(int(chord_len / step)):
            ts = start + j * step
            if ts > seconds + 1:
                break
            m = tones[pattern[j % len(pattern)] % len(tones)]
            a0 = int(ts * SR)
            ln = int(0.9 * SR)
            a1 = min(n, a0 + ln)
            tt = np.arange(a1 - a0) / SR
            f = _hz(m)
            vel = 0.9 if j % 4 == 0 else 0.6 + 0.2 * rng.random()
            pl = (np.sin(2 * math.pi * f * tt) + 0.25 * np.sin(4 * math.pi * f * tt)) * np.exp(-tt / 0.22)
            pl *= np.clip(tt / 0.004, 0, 1) * 0.085 * vel
            pan = 0.3 if j % 2 == 0 else 0.7
            mix_arp[a0:a1, 0] += pl * (1 - pan)
            mix_arp[a0:a1, 1] += pl * pan
        start += chord_len
        k += 1

    # yankılı gecikme (arpej): 3/8 vuruş sonra karşı kanalda tekrar
    dly = int(0.75 * beat * SR)
    echo = np.zeros_like(mix_arp)
    echo[dly:, 0] = mix_arp[:-dly, 1] * 0.38
    echo[dly:, 1] = mix_arp[:-dly, 0] * 0.38
    mix_arp += echo

    # hafif vuruş: her vuruşta yumuşak bir tok ses, ara sekizliklerde çok kısık tıkırtı
    nrng = np.random.default_rng(zlib.crc32(str(seed).encode()))
    kick_t = np.arange(int(0.3 * SR)) / SR
    kick = np.sin(2 * math.pi * (50 * kick_t + 70 * (1 - np.exp(-kick_t / 0.04)) * 0.04)) * np.exp(-kick_t / 0.10) * 0.14
    hat = nrng.standard_normal(int(0.05 * SR))
    hat = np.diff(hat, prepend=0)
    hat = np.convolve(hat, [0.25, 0.5, 0.25], mode="same")                 # tıkırtının sert ucunu yumuşat
    ht = np.arange(len(hat)) / SR
    hat = hat * np.exp(-ht / 0.015) * np.clip(ht / 0.002, 0, 1) * 0.045
    b = 0
    while b * beat < seconds + 1:
        p = int(b * beat * SR)
        if p + len(kick) < n:
            mix_dry[p:p + len(kick)] += kick[:, None]
        q = int((b + 0.5) * beat * SR)
        if q + len(hat) < n:
            mix_dry[q:q + len(hat), 0] += hat * 0.8
            mix_dry[q:q + len(hat), 1] += hat
        b += 1

    wet = _reverb(mix_pad + mix_arp, seed=rng.randrange(1 << 30))
    out = mix_pad * 0.75 + mix_arp * 0.8 + wet * 0.55 + mix_dry
    out = out[:int(seconds * SR)]
    fade = int(1.0 * SR)
    out[-fade:] *= np.linspace(1, 0, fade)[:, None]
    out = np.tanh(out * 0.9) / np.tanh(0.9)                   # çok hafif sınırlama (sıkıştırma yok)
    out *= 0.89 / max(1e-6, np.abs(out).max())
    pcm = (out * 32767).astype("<i2")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return Path(path)


if __name__ == "__main__":
    import sys
    make_track(sys.argv[1] if len(sys.argv) > 1 else "ornek_muzik.wav", 30, sys.argv[2] if len(sys.argv) > 2 else "astro")
