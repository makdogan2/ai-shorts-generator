---
description: Create a new Shorts channel folder (settings + hook-first scripts) in niches/
argument-hint: <topic> [number of videos] [en|tr]
---

Create a new niche for this Shorts Factory from: $ARGUMENTS

The first part is the topic. An optional number sets how many videos (default 10, max 20).
An optional `tr` means Turkish scripts and a Turkish voice; otherwise English.

Steps:

1. Read `CLAUDE.md`, then `niches/space/settings.json` and the first three entries of `niches/space/topics.json` as the reference for format and tone.
2. Pick a short English kebab-case folder name that is not already in `niches/`.
3. Write `niches/<folder>/settings.json` with every key listed in CLAUDE.md. Choose a voice that fits the topic
   (English: en-US-BrianNeural, en-US-GuyNeural, en-US-AndrewNeural, en-US-ChristopherNeural, en-GB-RyanNeural, en-US-AriaNeural, en-US-JennyNeural;
   Turkish: tr-TR-AhmetNeural or tr-TR-EmelNeural with "lang": "tr"), a highlight color and dark palettes that fit its mood,
   `music: true`, `sfx: true`, and a channel name suggestion.
4. Write `niches/<folder>/topics.json` following the script rules in CLAUDE.md exactly. Every first sentence is a hook of at most 7 words.
   Use only facts you are sure of; if you can search the web, verify each number. Leave out any fact you cannot verify instead of guessing.
5. Create `niches/<folder>/muzik/BURAYA-MUZIK-AT.txt` with the same text as the one in `niches/space/muzik/`.
6. Add `{"folder": "<folder>", "label": "<Short English label>"}` to `niches/index.json`.
7. Validate all three JSON files with the command in CLAUDE.md and fix any error.
8. Show a short summary: channel name, voice, and every hook (first sentence) as a list.
   Then tell the user to render with `calistir.bat <folder>` (or `python pipeline.py <folder>`).
