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

