# Shorts Factory (AI Shorts Generator)

Turns text scripts into vertical YouTube Shorts on Windows: Kokoro (local, open-source) or edge-tts voiceover, Pixabay/Pexels stock clips,
pop-in word-by-word captions, a progress bar, synthesized whoosh/boom effects, optional background music,
FFmpeg render at -14 LUFS, and an automatic quality check after every render.

## Layout

- `pipeline.py` — the engine. `python pipeline.py [niche ...]` renders every topic that has no video yet; `--list` shows progress;
  `--check [niche ...]` runs the quality check on finished videos. A video that fails the check is saved as `<slug>.HATALI.mp4`.
  `--validate [niche ...]` checks topics.json against the script rules below (errors exit with code 1).
  `--preview [niche ...]` writes `output/<folder>/onizleme.jpg`, a frame-per-second sheet of the newest videos (also written after every render).
  After every render, new videos get the next free `upload_slots` in `output/<folder>/program.json`, and
  `output/<folder>/yukleme_plani.txt` lists each one's time, title, description and YouTube tags. `--plan` shows the calendar.
  New videos are ordered so the same format (a sim template, or stock footage) is not published twice in a row.
- `niches/<folder>/settings.json` — channel settings (voice, rate, lang, colors, scene timing, music, sfx).
- `niches/<folder>/topics.json` — the scripts, one object per video.
- `niches/<folder>/muzik/` — optional niche-specific music (mp3 files are git-ignored).
- `niches/index.json` — the list of niches the web app (index.html + site.js, GitHub Pages) shows. Every niche must be listed here.
- `kurulum.bat` / `calistir.bat` — Windows setup and run scripts. Keep them ASCII with CRLF line endings.
- `output/<folder>/` — rendered videos (git-ignored). Fully code-drawn (sim) videos go to `output/<folder>-sim/`;
  both folders share one publishing calendar, so two videos never get the same slot.

## Script rules (every topic)

- 32-42 words; the first sentence is a hook of at most 7 words that makes the viewer think "no way" within 1.5 seconds
  (e.g. "Paper can reach the Moon."). Best hooks shrink or flip something everyone knows ("Mount Everest is tiny.").
  Then concrete numbers or details, talking to the viewer, ending on a twist.
- Make it loop: the last sentence should lead naturally back into the hook, so a viewer who keeps watching hears the
  first line as the answer (ending "...and that is why" before "Paper can reach the Moon.").
- Only well-established, verifiable facts. Numbers must be correct. No myths, rumors, medical, legal or investment advice.
  When web search is available, check every number before writing it.
- Plain text: no emojis, hashtags or quotes inside the script.
- Vary the hook wording. YouTube's spam policy forbids using automated tools to flood the platform with repetitive
  content, so two topics must not share a hook skeleton ("Jupiter could swallow 1,300 Earths." and "Saturn could swallow
  764 Earths." are the same skeleton); `--validate` warns about it. Rotate shapes: shrink ("Jupiter is tiny next to the Sun."),
  exact twist ("Exactly four Earths fit across Uranus."), flip ("The Moon is smaller than you think.").

## topics.json entry

```json
{
  "slug": "unique-kebab-case",
  "title": "Catchy title under 70 characters",
  "script": "…",
  "keywords": ["moon", "paper"],
  "visuals": ["folded paper", "stack of paper", "moon", "astronaut"],
  "highlight": ["Moon", "Paper"],
  "description": "One sentence.",
  "tags": "#space #moon #shorts"
}
```

`keywords`: 1-2 English words per stock-footage search (a list mixes clips from several searches); used when a
`visuals` search finds nothing.
`visuals`: exactly one entry per sentence of the script, in order (a sentence ends with . ! or ?). Each is a 1-3 word
English stock-footage search for something you can literally see while that sentence is spoken ("folded paper",
"astronaut", "city traffic"), never an abstract idea ("doubling", "relativity"). Every sentence becomes its own scene.
The first entry is the most important: it must show the hook's subject itself, big and clear ("saturn planet",
"sun surface", "mars planet"), because viewers decide to swipe in the first half second.
`highlight`: 2-4 words copied exactly from the script; numbers are highlighted automatically.

### Code-drawn scenes (`sim`)

Instead of stock footage, a sentence can be drawn by `sims.py` at true scale. Write `"sim:<step>"` as that sentence's
`visuals` entry and describe the scene in the topic's `sim` object. Numbers in the script must match what the sim
measures (the engine computes them from real sizes), so check both. Steps are never split, and a body appears when its
name is spoken, so name the bodies in the script.

```json
"visuals": ["sim:intro", "sim:stack", "sim:measure", "sim:overflow", "sim:stretch", "sim:spare"],
"sim": {"type": "lineup", "bottom": "earth", "top": "moon",
        "stack": ["mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune"],
        "near_km": 384400, "far_km": 405500}
```

`lineup` steps: `intro` (close on the bottom body, pull back to show the gap), `stack` (bodies pop in one by one),
`measure` (ruler counts the stack's total size), `overflow` (zoom to the top; red where the stack overlaps at
`near_km`), `stretch` (top body moves out to `far_km`; gap ruler turns green when everything fits), `spare` (zoom
on the room left over).

`compare` (`"sim": {"type": "compare", "small": "earth", "big": "jupiter", "count": 1321}`): the big body at true scale
with the small one just below it. `count` is the volume ratio (big / small), shown by the fill counter. Steps:
`intro` (slow pull-back on both; good hook frame), `point` (pulsing ring on the small body: "that tiny dot"),
`spot` (zoom onto the small body), `back` (zoom back out), `fill` (small bodies fill the big one bottom-up while the
counter runs to `count`), `across` (a row across the big body's diameter, counter to the whole number that fits),
`outro` (slow push-in; use for the loop sentence). Use `spot` and `back` as a pair so the camera does not jump.

`collapse` (`"sim": {"type": "collapse", "body": "sun", "mass_suns": 1, "compare": {"name": "Mount Everest", "km": 8.85}}`):
the body is squeezed to the event horizon of its own mass (5.9 km per solar mass, across) while the camera zooms
along ("ZOOM x..."). Steps: `intro`, `size` (diameter ruler), `crush`, `horizon` (flash, black hole with light
bending), `compare` (the `compare` object at the same scale: `"shape": "mountain"` (default, height),
`"coin"` (diameter) or `"ruler"` (a school ruler, length)), `outro`.

`giant` (`"sim": {"type": "giant", "holes": [{"name": "Sagittarius A*", "mass_suns": 4000000}, {"name": "M87*",
"mass_suns": 6500000000}], "fit_orbit": "Mercury"}`): black holes drawn at true scale against the planets' orbits.
Steps: `intro` (the biggest hole with Neptune's orbit inside: hook frame), `first` (the first hole where the Sun is),
`orbit` (the orbit it fits inside pulses), `zoom` (pull back past every planet), `grow` (the big hole grows from the
centre), `outro`. Masses: Event Horizon Telescope (Sgr A* about 4 million, M87* about 6.5 billion solar masses).

Bodies: sun, mercury, venus, earth, moon, mars, jupiter, saturn, uranus, neptune (NASA equatorial diameters, IAU
nominal Sun radius, in `sims.BODIES`). Never use NASA or other real images, logos or names of people in sims.

A niche with `"sim_only": true` in settings.json (Astro Pocket) makes only fully code-drawn videos: every `visuals`
entry must be `sim:<step>`, so pick topics that a template can show. Older stock-footage topics are skipped.

## settings.json keys

`channel`, `tts` ("kokoro": open-source voice that runs on the PC, English only, model downloaded once to `models/`;
"edge": Microsoft Edge voices, needs internet, any language), `voice` (Kokoro: af_heart, am_michael, am_fenrir, bm_george;
edge-tts: en-US-BrianNeural, en-GB-RyanNeural, tr-TR-AhmetNeural),
`rate` ("+8%"), `lang` ("en" or "tr"), `scene_seconds` (3.2), `first_scene_seconds` (1.5),
`fallback_queries` (3 generic English searches), `default_tags`, `highlight_color` ("#RRGGBB"),
`starfield` (true only for space/night themes), `palettes` (2-3 lists of three dark "0xRRGGBB" colors),
`music` (true/false), `music_gen` (true: when the muzik folders are empty, every video gets an original
track generated in code by `music.py`, so there is nothing to license and no Content ID claim), `music_rel_db` (-10), `music_fade_in` (0), `sfx` (true), `sfx_rel_db` (-8),
`caption_pop` (true), `progress_bar` ("top", "bottom" or false), `hook_title` (true: the hook sentence stays on screen
as a big title from the first frame), `zoom` (0.08: gentle push-in/pull-out on every scene, 0 to turn off),
`upload_slots` (["02:00", "22:00"]: publishing times for the upload plan), `base_tags` (channel tags added to every video),
`sim_only` (true: only code-drawn sim videos are made; topics with stock-footage visuals are skipped).

## Conventions

- Never commit music, API keys (`pixabay_key.txt`, `pexels_key.txt`) or rendered videos; `.gitignore` covers them.
- Validate JSON after editing: `python -c "import json,sys; [json.load(open(f, encoding='utf-8')) for f in sys.argv[1:]]" <files>`.
- To add a niche, use the `/new-niche` command. To add scripts to an existing niche, use `/more-topics <folder> [count]`.
- After editing a topics.json, run `python pipeline.py --validate <folder>`.
- After changing `pipeline.py` or `sims.py`, run `python -m pytest tests` (render tests need FFmpeg). GitHub Actions runs them on every push.
