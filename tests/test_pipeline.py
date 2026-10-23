"""Tests for the Shorts Factory engine.

Unit tests run anywhere. The render tests need ffmpeg/ffprobe on PATH and build a real
short video from synthetic clips, so they catch regressions such as captions vanishing
after a scene cut (FFmpeg 7+ filter re-init) that only show up in the final file.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline as P  # noqa: E402

SCRIPT = ("Paper can reach the Moon. Just fold it 42 times. Every fold doubles its thickness, "
          "so after 42 folds, a sheet a tenth of a millimeter thick becomes over 400,000 kilometers tall. "
          "You can't really fold it that much, but the math is real.")
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


def even_words(script, start=0.0, step=0.35):
    toks = [t.strip(".,!?") for t in script.split()]
    return [(start + i * step, start + i * step + step * 0.8, w) for i, w in enumerate(toks)]


# ------------------------------------------------------------------ word timing

def test_align_words_keeps_complete_timings():
    words = even_words(SCRIPT)
    out, ratio = P.align_words(words, SCRIPT, 20.0)
    assert ratio == 1.0
    assert [round(s, 3) for s, _, _ in out] == [round(s, 3) for s, _, _ in words]


@pytest.mark.parametrize("keep", [slice(0, 4), slice(0, 10)])
def test_align_words_fills_missing_tail(keep):
    """edge-tts sometimes drops the timings of the last words; every word must still get a caption."""
    full = even_words(SCRIPT)
    out, ratio = P.align_words(full[keep], SCRIPT, 20.0)
    assert len(out) == len(SCRIPT.split())
    assert ratio < 1
    starts = [s for s, _, _ in out]
    assert starts == sorted(starts)
    assert out[-1][0] < 20.0


def test_align_words_fills_gap_in_the_middle():
    full = even_words(SCRIPT)
    out, ratio = P.align_words(full[:5] + full[12:], SCRIPT, 20.0)
    assert len(out) == len(SCRIPT.split())
    assert all(out[i][0] <= out[i + 1][0] for i in range(len(out) - 1))
    assert out[5][2] == SCRIPT.split()[5].strip(".,!?")


# ------------------------------------------------------------------ scenes

def test_sentence_spans():
    spans = P.sentence_spans(SCRIPT)
    toks = SCRIPT.split()
    assert len(spans) == 4
    assert toks[spans[0][1]] == "Moon."
    assert spans[-1][1] == len(toks) - 1


def test_plan_scenes_without_visuals_uses_equal_scenes():
    cfg = dict(P.DEFAULTS, scene_seconds=3.2, first_scene_seconds=1.5)
    scenes = P.plan_scenes(even_words(SCRIPT), SCRIPT, 15.0, cfg, None)
    assert scenes[0] == (1.5, None)
    assert abs(sum(d for d, _ in scenes) - 15.0) < 1e-6


def test_plan_scenes_cuts_on_sentences_and_splits_long_ones():
    cfg = dict(P.DEFAULTS, scene_seconds=3.2)
    words = even_words(SCRIPT)
    total = words[-1][1] + 0.3
    visuals = ["moon", "folding paper", "stack of paper", "chalkboard"]
    scenes = P.plan_scenes(words, SCRIPT, total, cfg, visuals)
    assert abs(sum(d for d, _ in scenes) - total) < 1e-6
    assert scenes[0][1] == "moon"
    assert [q for _, q in scenes].count("stack of paper") >= 2      # the long sentence is split
    assert max(d for d, _ in scenes) <= cfg["scene_seconds"] * 1.4 + 1e-6


def test_plan_scenes_merges_very_short_sentence():
    script = "Saturn floats. Well. It is mostly hydrogen and helium so it is less dense than water."
    words = even_words(script, step=0.3)
    words[2] = (words[2][0], words[2][0] + 0.2, words[2][2])
    cfg = dict(P.DEFAULTS, scene_seconds=3.2)
    scenes = P.plan_scenes(words, script, words[-1][1] + 0.3, cfg, ["saturn", "bathtub", "gas planet"])
    assert "bathtub" not in [q for _, q in scenes]                  # "Well." is under 0.7 s


# ------------------------------------------------------------------ stock footage filters

@pytest.mark.parametrize("query,tags,ok", [
    ("folding paper", "paper, origami, fold, hands", True),
    ("folding paper", "grass, water, nature", False),
    ("stack of paper", "paper, money, stack, dollar", False),       # paper money is blocked
    ("stack of paper", "paper, stack, office", True),
    ("saturn", "space, stars, milky way", False),
    ("bathtub", "bath, green screen, chroma key", False),           # green screen is blocked
    ("earth from space", "earth, planet, space", True),
    ("math equations", "mathematics, equation, formula", True),
])
def test_relevant(query, tags, ok):
    assert P.relevant(query, tags) is ok


def test_tag_rank_prefers_main_subject():
    assert P.tag_rank("saturn", "saturn, planet") < P.tag_rank("saturn", "space, planets, saturn")


def test_clips_for_scenes_avoids_repeats_and_falls_back(monkeypatch):
    pools = {"moon": ["m1", "m2", "m3"], "paper": ["p1"], "nothing": [], "satellite": ["k1", "k2"]}
    monkeypatch.setattr(P, "fetch_clips", lambda q, fb, n=4: pools.get(q if isinstance(q, str) else q[0], [])[:n])
    monkeypatch.setattr(P, "hook_score", lambda c: 0)
    scenes = [(1.5, "moon"), (3, "paper"), (3, "paper"), (3, "nothing")]
    clips = P.clips_for_scenes(scenes, "satellite", [])
    assert len(clips) == 4
    assert clips[0] in pools["moon"] and clips[1] == "p1"
    assert clips[2] != "p1" and clips[3] in pools["satellite"]      # fallback to topic keywords


def test_clips_for_scenes_puts_best_hook_clip_first(monkeypatch):
    monkeypatch.setattr(P, "fetch_clips", lambda q, fb, n=4: ["dark", "still", "moving"][:n] if q == "moon" else [])
    monkeypatch.setattr(P, "hook_score", {"dark": -970, "still": 3.0, "moving": 9.5}.get)
    clips = P.clips_for_scenes([(1.5, "moon"), (3, "moon")], "x", [])
    assert clips == ["moving", "still"]


def test_clips_for_scenes_returns_empty_without_any_footage(monkeypatch):
    monkeypatch.setattr(P, "fetch_clips", lambda q, fb, n=4: [])
    assert P.clips_for_scenes([(3, None), (3, None)], "x", []) == []


# ------------------------------------------------------------------ caption images

def test_word_png_pop_sizes(tmp_path):
    from PIL import Image
    sizes = []
    for scale in (0.8, 1.12, 1.0):
        p = tmp_path / f"w{scale}.png"
        P.make_word_png("Moon", p, scale=scale)
        sizes.append(Image.open(p).size[0])
    assert sizes[0] < sizes[2] < sizes[1]


def test_title_png_wraps_long_hook(tmp_path):
    from PIL import Image
    p = tmp_path / "title.png"
    P.make_title_png("One planet rolls around the Sun on its side.", p, {"sun"}, "#FFD60A")
    w, h = Image.open(p).size
    assert w == P.W and 0 < h < P.H // 3


# ------------------------------------------------------------------ full render (needs ffmpeg)

def make_clip(path, color_tags):
    """2 s test clip; color_tags=True writes bt709 tags, False leaves them unset (like many stock clips)."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=720x1280:r=25:d=2",
           "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if color_tags:
        cmd += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
    subprocess.run(cmd + [str(path)], check=True)
    return path


@pytest.fixture
def niche(tmp_path, monkeypatch):
    folder = tmp_path / "niches" / "test"
    folder.mkdir(parents=True)
    (folder / "settings.json").write_text(json.dumps({"music": False, "sfx": True, "first_scene_seconds": 1.5,
                                                      "scene_seconds": 3.2}))
    (folder / "topics.json").write_text("[]")
    monkeypatch.setattr(P, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(P, "SFX_WHOOSH", tmp_path / "cache" / "whoosh.wav")
    monkeypatch.setattr(P, "SFX_BOOM", tmp_path / "cache" / "boom.wav")
    n = P.Niche(folder)
    n.out = tmp_path / "out"
    n.out.mkdir()
    return n


def voice(niche, seconds=7.0):
    mp3 = niche.out / "test.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=220:d={seconds}",
                    "-ar", "24000", str(mp3)], check=True)
    return P.duration(mp3) + 0.3


@needs_ffmpeg
def test_render_keeps_captions_across_cuts_with_mixed_color_tags(niche, tmp_path):
    """Regression: clips with different colour tags made FFmpeg 7+ re-init the filter graph at the
    cut and every caption after it disappeared."""
    clips = [make_clip(tmp_path / "a.mp4", False), make_clip(tmp_path / "b.mp4", True),
             make_clip(tmp_path / "c.mp4", False)]
    script = "Saturn could float in your bathtub. It is mostly hydrogen and helium so it is lighter than water."
    total = voice(niche)
    words, _ = P.align_words([], script, total - 0.3)
    hook = (" ".join(script.split()[:6]), words[6][0])
    scenes = P.plan_scenes(words, script, total, niche.cfg, ["a", "b"])
    P.render(niche, "test", total, [d for d, _ in scenes], clips, words, {"saturn"}, hook=hook)
    video = niche.out / "test.mp4"
    assert video.exists()
    assert P.check_video(video, total, (hook[1] + 0.1, words[-1][1] - 0.1)) == []
    assert not list(niche.out.glob("_w*.png"))                      # temporary files are cleaned up


@needs_ffmpeg
def test_quality_check_catches_missing_captions(niche, tmp_path):
    clips = [make_clip(tmp_path / "a.mp4", False)]
    total = voice(niche, 5.0)
    P.render(niche, "test", total, [total], clips, [], set())         # no captions at all
    problems = P.check_video(niche.out / "test.mp4", total)
    assert any("altyazı" in p for p in problems)


@needs_ffmpeg
def test_quality_check_catches_green_screen(niche, tmp_path):
    green = tmp_path / "green.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x22dd22:s=720x1280:r=25:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(green)], check=True)
    assert P.green_screen_seconds(green) >= 1.5


# ------------------------------------------------------------------ topics validator

GOOD_SCRIPT = ("Paper can reach the Moon. Just fold it 42 times. Every fold doubles its thickness, so a sheet a tenth "
               "of a millimeter thick ends up over 400,000 kilometers tall. You cannot really fold it that much, but the math is real.")


def good_topic(**over):
    t = {"slug": "paper-moon", "title": "Paper can reach the Moon", "script": GOOD_SCRIPT, "keywords": ["moon"],
         "visuals": ["moon", "folding paper", "stack of paper", "chalkboard"], "highlight": ["Moon", "Paper"],
         "description": "Doubling.", "tags": "#space #shorts"}
    t.update(over)
    return t


def test_validate_accepts_good_topic():
    errors, warnings = P.validate_topics([good_topic()])
    assert errors == [] and warnings == []


def test_validate_catches_problems():
    errors, warnings = P.validate_topics([
        good_topic(visuals=["moon"]),                                   # 4 sentences, 1 search
        good_topic(slug="paper-moon", highlight=["Jupiter"]),           # duplicate slug, highlight not in script
        good_topic(slug="Bad Slug", script="Did you know #space is " + "very " * 30 + "big?"),
        {"slug": "x"},
    ])
    text = " | ".join(errors)
    assert "visuals" in text and "aynı slug" in text and "Jupiter" in text
    assert "kebab-case" in text and "hashtag" in text and "eksik alan" in text


def test_validate_warns_on_long_hook():
    long_hook = "The Moon is drifting away from the Earth every year. " + GOOD_SCRIPT.split(". ", 1)[1]
    _, warnings = P.validate_topics([good_topic(script=long_hook, highlight=["Moon"])])
    assert any("kanca" in w for w in warnings)


def test_repo_topics_have_no_errors():
    for folder in sorted((Path(P.__file__).parent / "niches").iterdir()):
        f = folder / "topics.json"
        if f.exists():
            errors, _ = P.validate_topics(json.loads(f.read_text(encoding="utf-8")))
            assert errors == [], (folder.name, errors)


@needs_ffmpeg
def test_render_survives_broken_zoom_and_broken_clip(niche, tmp_path):
    """A scene that FFmpeg cannot prepare must not stop the video: retry without zoom, then use a plain background."""
    good = make_clip(tmp_path / "a.mp4", False)
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    niche.cfg["zoom"] = "1/("                                       # an expression FFmpeg rejects
    total = voice(niche, 5.0)
    words, _ = P.align_words([], "One two three four five six seven eight.", total - 0.3)
    P.render(niche, "test", total, [total / 2, total / 2], [good, broken], words, set())
    video = niche.out / "test.mp4"
    assert video.exists() and abs(P.duration(video) - total) < 0.5


@needs_ffmpeg
def test_preview_sheet_flags_dark_first_frame(tmp_path):
    from PIL import Image
    bright = make_clip(tmp_path / "bright.mp4", False)
    dark = tmp_path / "dark.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=720x1280:r=25:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dark)], check=True)
    out = P.make_preview([bright, dark], tmp_path / "onizleme.jpg")
    w, h = Image.open(out).size
    assert out.exists() and w > 300 and h > 400                     # two rows


# ------------------------------------------------------------------ upload plan

def test_next_slots_skips_taken_and_past():
    import datetime as dt
    now = dt.datetime(2026, 10, 7, 10, 30)
    taken = {dt.datetime(2026, 10, 7, 22, 0)}
    got = P.next_slots(taken, ["02:00", "22:00"], 3, now)
    assert got == [dt.datetime(2026, 10, 8, 2, 0), dt.datetime(2026, 10, 8, 22, 0), dt.datetime(2026, 10, 9, 2, 0)]


def test_write_plan_continues_calendar(tmp_path):
    import datetime as dt
    class N:  # minimal niche
        out = tmp_path
        cfg = dict(P.DEFAULTS, base_tags=["space facts"])
    t1 = good_topic(slug="a"); t2 = good_topic(slug="b")
    now = dt.datetime(2026, 10, 7, 10, 30)
    _, prog = P.write_plan(N, [t1], now)
    assert prog["a"] == "2026-10-07T22:00"
    path, prog = P.write_plan(N, [t2], now)
    assert prog["b"] == "2026-10-08T02:00"
    text = path.read_text(encoding="utf-8")
    assert "Paper can reach the Moon" in text and "space facts" in text and "Per 08.10 02:00" in text


def test_youtube_tags_unique_and_limited():
    tags = P.youtube_tags(good_topic(), dict(P.DEFAULTS, base_tags=["space facts", "moon"]))
    parts = tags.split(", ")
    assert len(parts) == len(set(parts)) and "moon" in parts and len(tags) <= 450


# ------------------------------------------------------------------ hook clip scoring

def _lavfi(path, src):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


@needs_ffmpeg
def test_hook_score_prefers_bright_moving_clips(tmp_path):
    still = _lavfi(tmp_path / "still.mp4", "color=c=0x8899aa:s=360x640:r=25:d=2")
    moving = _lavfi(tmp_path / "moving.mp4", "testsrc2=s=360x640:r=25:d=2")
    dark = _lavfi(tmp_path / "dark.mp4", "color=c=0x050505:s=360x640:r=25:d=2")
    light, motion = P.first_second(still)
    assert light > P.DARK_HOOK and motion < 0.5
    assert P.first_second(moving)[1] > 1
    assert P.hook_score(moving) > P.hook_score(still) > P.hook_score(dark)


# ------------------------------------------------------------------ Kokoro voice timing

def _tone(sec, sr=24000):
    import numpy as np
    t = np.arange(int(sec * sr)) / sr
    return (0.5 * np.sin(2 * np.pi * 220 * t)).astype("float32")


def test_sentence_word_times_split_on_comma_pause():
    import numpy as np
    sr = 24000
    audio = np.concatenate([_tone(0.5), np.zeros(int(0.2 * sr), "float32"), _tone(0.6)])
    words = P.sentence_word_times(["On", "Mars,", "Olympus", "rises."], audio, sr, offset=1.0)
    assert [w[2] for w in words] == ["On", "Mars", "Olympus", "rises"]
    assert words[0][0] == 1.0 and abs(words[1][1] - 1.5) < 0.02      # first clause ends where the pause starts
    assert abs(words[2][0] - 1.7) < 0.02 and abs(words[3][1] - 2.3) < 0.02
    assert all(a < b for a, b, _ in words)


def test_sentence_word_times_without_pause_spreads_by_length():
    words = P.sentence_word_times(["I", "kilometers"], _tone(1.0), 24000)
    assert words[1][1] - words[1][0] > 3 * (words[0][1] - words[0][0])


def test_rate_to_speed():
    assert P._rate_to_speed("+8%") == 1.08 and P._rate_to_speed("-10%") == 0.9 and P._rate_to_speed(None) == 1.0


def test_settings_errors_for_kokoro():
    ok = {**P.DEFAULTS, "tts": "kokoro", "voice": "af_heart"}
    assert P.settings_errors(ok) == []
    assert P.settings_errors({**ok, "lang": "tr"})
    assert P.settings_errors({**ok, "voice": "en-US-BrianNeural"})
    assert P.settings_errors({**P.DEFAULTS, "tts": "azure"})



# ------------------------------------------------------------------ code-drawn scenes (sims.py)

import sims  # noqa: E402

LINEUP = {"type": "lineup", "bottom": "earth", "top": "moon",
          "stack": ["mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune"],
          "near_km": 384400, "far_km": 405500}


def test_lineup_numbers_match_the_script():
    sim = sims.Lineup(LINEUP)
    assert round(sim.stack_len) == 387941                 # NASA equatorial diameters
    assert sim.gap_near < sim.stack_len < sim.gap_far      # too wide on average, fits at apogee
    assert round(sim.gap_far, -3) == 397000


def test_sim_validate_catches_bad_steps_and_bodies():
    assert sims.validate(LINEUP, ["intro", "stack", "spare"]) == []
    errs = sims.validate(dict(LINEUP, stack=["pluto"]), ["dance"])
    assert any("dance" in e for e in errs) and any("pluto" in e for e in errs)
    assert sims.validate(None, ["intro"])                  # sim: steps without a sim spec


def test_plan_scenes_keeps_sim_scene_whole():
    script = "Every planet fits here. " + "Stack them one by one in a very long line right now " * 2 + "."
    words = even_words(script)
    cfg = dict(P.DEFAULTS, scene_seconds=3.2)
    scenes = P.plan_scenes(words, script, words[-1][1] + 0.3, cfg, ["sim:intro", "sim:stack"])
    assert [q for _, q in scenes] == ["sim:intro", "sim:stack"]   # long sentence is not split


def test_clips_for_scenes_passes_sims_through(monkeypatch):
    def no_fetch(*a, **k):
        raise AssertionError("no stock search for sim scenes")
    monkeypatch.setattr(P, "fetch_clips", no_fetch)
    assert P.clips_for_scenes([(1.5, "sim:intro"), (3, "sim:stack")], "x", []) == ["sim:intro", "sim:stack"]
    monkeypatch.setattr(P, "fetch_clips", lambda q, fb, n=4: ["m1", "m2"][:n] if q == "moon" else [])
    monkeypatch.setattr(P, "hook_score", lambda c: 0)
    assert P.clips_for_scenes([(1.5, "sim:intro"), (3, "moon")], "x", []) == ["sim:intro", "m1"]


def test_validate_checks_sim_topics():
    t = dict(good_topic(), sim=LINEUP)
    t["visuals"] = ["sim:intro"] + t["visuals"][1:]
    assert P.validate_topics([t])[0] == []
    t["visuals"] = ["sim:nope"] + t["visuals"][1:]
    assert any("nope" in e for e in P.validate_topics([t])[0])


def test_youtube_tags_skip_sim_steps():
    t = dict(good_topic(), sim=LINEUP)
    t["visuals"] = ["sim:intro"] + t["visuals"][1:]
    assert "sim" not in P.youtube_tags(t, P.DEFAULTS)


def test_lineup_planets_appear_when_named():
    sim = sims.Lineup(LINEUP)
    words = [(0.0, "Stack"), (0.5, "Jupiter,")]
    st = sim.state("stack", 0.45, words)
    assert st["pop"][3] == 0 and sim.state("stack", 0.7, words)["pop"][3] > 0.9


@needs_ffmpeg
def test_sim_hook_scene_is_bright_and_has_right_length(tmp_path):
    out = tmp_path / "intro.mp4"
    sims.render_step(LINEUP, "intro", 0.4, out, P.SEG_NORM)
    assert abs(P.duration(out) - 0.4) < 0.1
    assert P.first_frame_light(out) > P.DARK_HOOK


COMPARE = {"type": "compare", "small": "earth", "big": "jupiter", "count": 1321}


def test_compare_fits_eleven_earths_across_jupiter():
    sim = sims.Compare(COMPARE)
    assert sim.across == 11 and len(sim.fill_pts) > 50
    assert sims.Compare(dict(COMPARE, big="sun")).across == 109


def test_compare_validate():
    assert sims.validate(COMPARE, list(sims.TEMPLATES["compare"])) == []
    assert sims.validate(dict(COMPARE, small="jupiter", big="earth"), ["intro"])
    assert any("count" in e for e in sims.validate(dict(COMPARE, count=None), ["fill"]))


def test_all_sim_detects_fully_drawn_topics():
    stock, sim = good_topic(), dict(good_topic(slug="sim-one"), sim=LINEUP)
    sim["visuals"] = ["sim:intro"] * len(sim["visuals"])
    assert not P.all_sim(stock) and P.all_sim(sim)


def test_sim_videos_get_their_own_folder_and_share_the_calendar(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "OUT_ROOT", tmp_path)
    sim = dict(good_topic(slug="sim-one"), sim=LINEUP)
    sim["visuals"] = ["sim:intro"] * len(sim["visuals"])
    niche = P.Niche.__new__(P.Niche)
    niche.name, niche.cfg, niche.topics, niche.out = "space", dict(P.DEFAULTS), [good_topic(), sim], tmp_path / "space"
    stock_v, sim_v = P.split_outputs([niche])
    assert sim_v.out == tmp_path / "space-sim" and [t["slug"] for t in sim_v.topics] == ["sim-one"]
    assert [t["slug"] for t in stock_v.topics] == ["paper-moon"]
    now = P.dt.datetime(2026, 10, 11, 12, 0)
    for v in (stock_v, sim_v):
        v.out.mkdir()
        P.write_plan(v, v.topics, now)
    a = json.loads((stock_v.out / "program.json").read_text())["paper-moon"]
    b = json.loads((sim_v.out / "program.json").read_text())["sim-one"]
    assert a != b


# ------------------------------------------------------------------ variety guards

def test_hook_pattern_flags_repeated_skeleton():
    assert P.hook_pattern("Jupiter could swallow 1,300 Earths. More.") == P.hook_pattern("Saturn could swallow 764 Earths.")
    assert P.hook_pattern("Mount Everest is tiny.") != P.hook_pattern("Jupiter is tiny next to the Sun.")
    base = {"title": "t", "keywords": ["x"], "highlight": ["Saturn"], "description": "d", "tags": "#a"}
    topics = [dict(base, slug="a", script="Jupiter could swallow 1,300 Earths. Saturn too."),
              dict(base, slug="b", script="Saturn could swallow 764 Earths. Saturn too.")]
    _, warnings = P.validate_topics(topics)
    assert any("kanca kalıbı" in w and "a, b" in w for w in warnings)


def test_spread_formats_avoids_back_to_back():
    topics = [{"slug": "c1", "sim": {"type": "compare"}}, {"slug": "c2", "sim": {"type": "compare"}},
              {"slug": "c3", "sim": {"type": "compare"}}, {"slug": "l1", "sim": {"type": "lineup"}}]
    order = [t["slug"] for t in P.spread_formats(topics)]
    assert sorted(order) == ["c1", "c2", "c3", "l1"]
    assert order[:3] == ["c1", "l1", "c2"]


def test_compare_draws_saturn_rings():
    img = sims.Compare({"type": "compare", "small": "earth", "big": "saturn", "count": 764}).frame("intro", 1.0)
    big = sims.Compare.BIG_R
    # halkanın gezegen diskinin dışında kalan kısmı (diskin sağında, merkezin biraz altında) arka plandan parlak
    x, y = int(sims.W / 2 + big * 1.15), int(sims.Compare.BIG_Y + big * 0.32)
    assert max(img.getpixel((x, y))) > 90


# ------------------------------------------------------------------ black holes and generated music

def test_horizon_sizes_match_known_values():
    assert abs(sims.horizon_km(1) - 5.9) < 0.1                       # the Sun squeezed: about 6 km across
    assert 23e6 < sims.horizon_km(4e6) < 24e6                        # Sagittarius A*: about 24 million km
    assert 38e9 < sims.horizon_km(6.5e9) < 39e9                      # M87*: about 38 billion km
    assert sims.horizon_km(4e6) / 2 < dict(sims.ORBITS)["Mercury"]   # Sgr A* fits inside Mercury's orbit
    assert sims.horizon_km(6.5e9) / 2 > 4 * dict(sims.ORBITS)["Neptune"]


def test_black_hole_templates_validate_and_draw():
    c = {"type": "collapse", "body": "sun", "mass_suns": 1, "compare": {"name": "Mount Everest", "km": 8.85}}
    g = {"type": "giant", "holes": [{"name": "Sagittarius A*", "mass_suns": 4e6}, {"name": "M87*", "mass_suns": 6.5e9}]}
    assert sims.validate(c, list(sims.TEMPLATES["collapse"])) == []
    assert sims.validate(g, list(sims.TEMPLATES["giant"])) == []
    assert sims.validate(dict(g, holes=g["holes"][:1]), ["intro"])
    img = sims.Collapse(c).frame("horizon", 1.0)
    assert img.getpixel((sims.W // 2, sims.Collapse.CY)) == (0, 0, 0)  # the horizon itself is black
    assert sum(img.getpixel((sims.W // 2 + 166, sims.Collapse.CY))) > 300  # bright photon ring just outside


def test_collapse_ruler_compare_is_drawn_to_scale():
    # Neptune (GM ratio 6.8351 / 132712) squeezed: about 30.4 cm across, next to a 30 cm school ruler
    c = {"type": "collapse", "body": "neptune", "mass_suns": 6.8351 / 132712,
         "compare": {"name": "School ruler", "km": 3.0e-4, "shape": "ruler"}}
    assert sims.validate(c, list(sims.TEMPLATES["collapse"])) == []
    assert sims.validate(dict(c, compare=dict(c["compare"], shape="banana")), ["compare"])
    assert sims.km_label(sims.horizon_km(c["mass_suns"])) == "30.4 cm"
    img = sims.Collapse(c).frame("compare", 1.0)
    # the hole sits left of centre (r = 110 px); the ruler stands to its right, same scale, so almost as tall
    r, cy, bx = 110, sims.Collapse.CY + sims.Collapse.CMP_DROP, int(sims.W * 0.70)
    wood = img.getpixel((bx + 5, cy + r - int(2 * r * 3.0e-4 / sims.horizon_km(c["mass_suns"]) / 2)))
    assert wood[0] > 150 and wood[0] > wood[2]                      # wood-coloured strip
    assert sum(img.getpixel((bx + 5, cy - r - 15))) < sum(wood)     # nothing above the ruler's top


def test_generated_music_is_deterministic_and_loud_enough(tmp_path):
    import music
    a, b = music.make_track(tmp_path / "a.wav", 4, "slug-1"), music.make_track(tmp_path / "b.wav", 4, "slug-1")
    assert a.read_bytes() == b.read_bytes()
    assert music.make_track(tmp_path / "c.wav", 4, "slug-2").read_bytes() != a.read_bytes()
    assert P.duration(a) == pytest.approx(4, abs=0.05)



def test_lineup_group_label_stays_out_of_caption_band():
    # 30 identical Earths share one "30 EARTHS" label; it must not sit behind the word-by-word captions
    import numpy as np
    spec = {"type": "lineup", "bottom": "earth", "top": "moon", "stack": ["earth"] * 30,
            "near_km": 384400, "far_km": 405500}
    lo, hi = sims.CAPTION_BAND
    for step, u in (("stack", 1.0), ("measure", 1.0)):
        a = np.asarray(sims.Lineup(spec).frame(step, u))
        right = a[lo + 20:hi - 20, sims.W // 2 + 40:sims.W // 2 + 330].min(axis=2) > 200
        assert not (right.sum(axis=1) > 20).any(), step   # stray stars are a few px; label text is a wide row


def test_collapse_comparison_sits_below_the_captions():
    import numpy as np
    c = {"type": "collapse", "body": "neptune", "mass_suns": 6.8351 / 132712,
         "compare": {"name": "School ruler", "km": 3.0e-4, "shape": "ruler"}}
    sim = sims.Collapse(c)
    assert sim.state("intro", 1.0)["label"] == 0          # no planet name under the hook title
    a = np.asarray(sim.frame("compare", 1.0))
    band = a[860:1060].max(axis=2)                        # captions: 140 px words centred on H/2, with pop
    assert (band > 120).mean() < 0.002                    # caption band left clear: only stars


def test_collapse_comparison_labels_never_cross_the_captions():
    # the yellow/green size pills only show once the comparison has dropped below the caption band
    import numpy as np
    c = {"type": "collapse", "body": "neptune", "mass_suns": 6.8351 / 132712,
         "compare": {"name": "School ruler", "km": 3.0e-4, "shape": "ruler"}}
    sim = sims.Collapse(c)
    for step in ("compare", "outro"):
        for u in np.linspace(0, 1, 21):
            a = np.asarray(sim.frame(step, float(u)))[860:1060].astype(int)
            r, g, b = a[..., 0], a[..., 1], a[..., 2]
            pill = ((r > 180) & (g > 150) & (b < 90)) | ((g > 180) & (r < 150) & (b < 170))
            assert pill.sum() < 40, (step, round(float(u), 2), int(pill.sum()))


def test_km_label_goes_down_to_millimeters():
    # the Moon squeezed to its event horizon is about 0.2 mm across
    assert sims.km_label(sims.horizon_km(3.6943e-08)) == "0.2 mm"
    assert sims.km_label(1.8e-5) == "1.8 cm"


def test_compare_near_equal_bodies_both_fit_on_screen():
    # Venus is 95% of Earth: both must be fully visible, the small one below the big one, not off-screen
    c = sims.Compare({"type": "compare", "small": "venus", "big": "earth", "steps": ["intro", "point", "outro"]})
    s, cy = c.full
    big_top = sims.H / 2 - (c.rb - cy) * s
    small_bottom = sims.H / 2 - (c.small_y - c.rs - cy) * s
    assert big_top > 450 and small_bottom < sims.H - 40
    # without an across step the outro must not draw a row of copies over the big body
    assert c.state("outro", 0.3)["row_a"] == 0.0
    j = sims.Compare(dict(COMPARE))   # small body far smaller: layout unchanged, row shown as before
    assert j.BIG_R == sims.Compare.BIG_R and j.state("outro", 0.3)["row_a"] > 0


def test_compare_across_without_fill_step_shows_no_filled_disk():
    # the Sun is 109 Earths wide: spot/back then across, no fill step; the across scene must not flash a full disk
    spec = dict(COMPARE, big="sun", steps=["intro", "point", "spot", "back", "across", "outro"])
    assert sims.Compare(spec).state("across", 0.0)["fill_a"] == 0.0
    assert sims.Compare(dict(COMPARE)).state("across", 0.0)["fill_a"] == 1.0   # after a fill step: fades out as before


def test_compare_small_saturn_keeps_its_rings():
    c = sims.Compare({"type": "compare", "small": "saturn", "big": "sun"})
    img = c.frame("point", 0.0)
    s, cy = c.full
    y = sims.H / 2 - (c.small_y - cy) * s
    r = c.rs * s
    # just right of the planet's disk, on the ring plane: brighter than the starry background
    assert max(img.getpixel((int(sims.W / 2 + r * 1.6), int(y + 1)))) > 70


def test_spread_formats_splits_an_unavoidable_majority_evenly():
    # 10 compare + 4 others: some repeats are unavoidable, but never a long run at the end of the week
    topics = [{"slug": f"c{i}", "sim": {"type": "compare"}} for i in range(10)] + \
             [{"slug": f"x{i}", "sim": {"type": "collapse"}} for i in range(3)] + [{"slug": "l0", "sim": {"type": "lineup"}}]
    order = P.spread_formats(topics)
    assert sorted(t["slug"] for t in order) == sorted(t["slug"] for t in topics)
    run = best = 1
    for a, b in zip(order, order[1:]):
        run = run + 1 if P.format_key(a) == P.format_key(b) else 1
        best = max(best, run)
    assert best == 2


# ------------------------------------------------------------------ sky, engulf and map templates

SKY = {"type": "sky", "body": "jupiter", "distance_km": 384400, "ground": "city", "fov": 40,
       "compare": {"body": "moon", "distance_km": 384400, "name": "Our Moon", "than": "our Moon"}}


def test_sky_angles_match_real_sizes():
    s = sims.Sky(SKY)
    assert abs(s.deg - 21.07) < 0.05 and abs(s.cdeg - 0.518) < 0.005      # Jupiter at the Moon's distance
    assert round(s.ratio) == 41
    rings = sims.Sky(dict(SKY, body="saturn", measure="rings"))
    assert 39.0 < rings.deg_m < 39.4 and round(rings.ratio) == 76          # A ring outer edge, 136,780 km
    earth = sims.Sky(dict(SKY, body="earth", ground="moon", fov=6))
    assert abs(earth.deg - 1.90) < 0.01 and round(earth.ratio) == 4
    assert sims.validate(SKY, list(sims.TEMPLATES["sky"])) == []
    assert sims.validate(dict(SKY, ground="mars"), ["intro"])


def test_sky_hook_frame_is_bright_and_draws_every_step():
    import numpy as np
    s = sims.Sky(SKY)
    assert np.asarray(s.frame("intro", 0.0).convert("L")).mean() > 35       # pipeline's dark-hook threshold
    for step in sims.TEMPLATES["sky"]:
        assert s.frame(step, 0.5).size == (sims.W, sims.H)


def test_engulf_moon_orbit_fits_inside_the_sun():
    e = sims.Engulf({"type": "engulf"})
    assert 2 * e.orbit < 2 * e.rb                                           # 768,800 km < 1,391,400 km
    assert 2 * e.rb * e.s_full < sims.W                                     # the whole Sun stays on screen
    for step in sims.TEMPLATES["engulf"]:
        assert e.frame(step, 0.5).size == (sims.W, sims.H)


def test_map_country_widths_and_fit():
    usa = sims.MapOver({"type": "map", "country": "usa"})
    assert 4500 < usa.span_km < 4800 and not usa.fits                      # lower 48 are wider than the Moon
    ph = sims.MapOver({"type": "map", "country": "philippines", "measure": "height"})
    assert ph.fits and ph.span_km < 3475
    assert (usa.box[1] - usa.box[0]) * usa.s < sims.W                       # the outline stays on screen
    assert sims.validate({"type": "map", "country": "atlantis"}, ["intro"])
    for step in sims.TEMPLATES["map"]:
        assert usa.frame(step, 0.5).size == (sims.W, sims.H)


def test_sky_compare_label_stays_out_of_the_caption_band():
    import numpy as np
    for spec in (SKY, dict(SKY, body="earth", ground="moon", fov=6)):
        sky = sims.Sky(spec)
        top = int(sky.cy + sky.cr + 100)                     # below the pulsing ring round the small body
        a = np.asarray(sky.frame("compare", 0.8))[max(top, sims.CAPTION_BAND[0]):sims.CAPTION_BAND[1]].astype(int)
        r, g, b = a[..., 0], a[..., 1], a[..., 2]
        yellow = (r > 200) & (g > 170) & (b < 80)            # the angle pill is yellow on dark
        assert yellow.sum() < 40
