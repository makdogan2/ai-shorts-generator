# Shorts Factory (AI Shorts Generator)

Turns text scripts into vertical YouTube Shorts on Windows: edge-tts voiceover, Pixabay/Pexels stock clips,
pop-in word-by-word captions, a progress bar, synthesized whoosh/boom effects, optional background music,
FFmpeg render at -14 LUFS, and an automatic quality check after every render.

## Layout

- `pipeline.py` — the engine. `python pipeline.py [niche ...]` renders every topic that has no video yet; `--list` shows progress;
  `--check [niche ...]` runs the quality check on finished videos. A video that fails the check is saved as `<slug>.HATALI.mp4`.
- `niches/<folder>/settings.json` — channel settings (voice, rate, lang, colors, scene timing, music, sfx).
- `niches/<folder>/topics.json` — the scripts, one object per video.
- `niches/<folder>/muzik/` — optional niche-specific music (mp3 files are git-ignored).
- `niches/index.json` — the list of niches the web app (index.html + site.js, GitHub Pages) shows. Every niche must be listed here.
- `kurulum.bat` / `calistir.bat` — Windows setup and run scripts. Keep them ASCII with CRLF line endings.
- `output/<folder>/` — rendered videos (git-ignored).

## Script rules (every topic)

- 32-42 words; the first sentence is a hook of at most 7 words that makes the viewer think "no way" within 1.5 seconds
  (e.g. "Paper can reach the Moon."). Best hooks shrink or flip something everyone knows ("Mount Everest is tiny.").
  Then concrete numbers or details, talking to the viewer, ending on a twist.
- Make it loop: the last sentence should lead naturally back into the hook, so a viewer who keeps watching hears the
  first line as the answer (ending "...and that is why" before "Paper can reach the Moon.").
- Only well-established, verifiable facts. Numbers must be correct. No myths, rumors, medical, legal or investment advice.
  When web search is available, check every number before writing it.
- Plain text: no emojis, hashtags or quotes inside the script.

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
`highlight`: 2-4 words copied exactly from the script; numbers are highlighted automatically.

## settings.json keys

`channel`, `voice` (edge-tts voice, e.g. en-US-BrianNeural, en-US-GuyNeural, en-GB-RyanNeural, tr-TR-AhmetNeural),
`rate` ("+8%"), `lang` ("en" or "tr"), `scene_seconds` (3.2), `first_scene_seconds` (1.5),
`fallback_queries` (3 generic English searches), `default_tags`, `highlight_color` ("#RRGGBB"),
`starfield` (true only for space/night themes), `palettes` (2-3 lists of three dark "0xRRGGBB" colors),
`music` (true/false), `music_rel_db` (-10), `music_fade_in` (0), `sfx` (true), `sfx_rel_db` (-8),
`caption_pop` (true), `progress_bar` ("top", "bottom" or false), `hook_title` (true: the hook sentence stays on screen
as a big title from the first frame), `zoom` (0.08: gentle push-in/pull-out on every scene, 0 to turn off).

## Conventions

- Never commit music, API keys (`pixabay_key.txt`, `pexels_key.txt`) or rendered videos; `.gitignore` covers them.
- Validate JSON after editing: `python -c "import json,sys; [json.load(open(f, encoding='utf-8')) for f in sys.argv[1:]]" <files>`.
- To add a niche, use the `/new-niche` command.
- After changing `pipeline.py`, run `python -m pytest tests` (render tests need FFmpeg). GitHub Actions runs them on every push.
