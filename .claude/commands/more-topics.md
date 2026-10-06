---
description: Add new hook-first, fact-checked scripts to an existing niche's topics.json
argument-hint: <niche folder> [number of videos]
---

Add new video scripts to an existing niche of this Shorts Factory: $ARGUMENTS

The first word is the niche folder in `niches/` (for example `space`). An optional number sets how many new
topics to add (default 10, max 20). If the folder does not exist, list the folders in `niches/` and stop.

Steps:

1. Read `CLAUDE.md` (script rules and the topics.json entry format), then `niches/<folder>/settings.json` and the
   whole `niches/<folder>/topics.json`. Note every existing slug, title, hook (first sentence) and core fact, so
   nothing new repeats a topic or a fact the channel already covered.
2. Pick the new topics. Favour the pattern that performs best on this channel: a familiar thing everyone knows,
   shrunk, flipped or compared in a surprising way ("Mount Everest is tiny.", "You've never seen the Sun.").
   Name the subject in the hook itself; avoid vague hooks like "this star". Mix sub-topics so the batch is varied.
3. For each topic write the entry exactly as CLAUDE.md describes:
   - `script`: 32-42 words. First sentence is the hook, at most 7 words. Then concrete numbers, talking to the
     viewer. The last sentence leads naturally back into the hook so the video loops.
   - `visuals`: exactly one 1-3 word English stock-footage search per sentence, something literally visible
     while that sentence is spoken. The first one must show the hook's subject itself ("saturn planet", not "space").
   - `keywords`, `highlight` (2-4 words copied exactly from the script), `title` (under 70 characters, close to
     the hook), `description` (one sentence), `tags` (3 topic hashtags plus #shorts), and a unique kebab-case `slug`.
   - Keep the language the niche uses (`lang` in settings.json).
4. Check the facts. Search the web for every number and claim before keeping it, and prefer primary or
   well-known sources (space agencies, encyclopedias, university pages). Rewrite or drop any topic whose
   numbers you cannot confirm. Never guess.
5. Append the new entries to the end of `niches/<folder>/topics.json`. Do not change or reorder existing entries.
6. Run `python pipeline.py --validate <folder>` and fix every error in the new entries; fix warnings in the new
   entries too (word count, hook length, title length). Ignore warnings on older entries.
7. Show a short summary: each new hook as a list, with the source you used for its key number.
   Then tell the user to render with `calistir.bat <folder>` (or `python pipeline.py <folder>`).
