"""
yazar.py — Claude API ile yeni Shorts senaryoları üretir, doğrular ve topics.json'a ekler.

`/more-topics` komutunun yaptığı işi Claude Code aboneliği yerine Claude Platform'daki aylık API kredisiyle yapar.
Akış: kuralları + mevcut konuları + sayı tablosunu modele ver → yeni konuları iste (tool use, topics.json şeması)
→ pipeline.validate_topics ile denetle, hataları modele geri verip düzelttir → her konunun sayılarını web
aramasıyla kontrol ettir → onay al → topics.json'a ekle → `pipeline.py --validate` çalıştır → rapor yaz.

Kullanım (venv açıkken, repo kökünde):
  python yazar.py space            # 10 yeni konu
  python yazar.py space 5          # 5 yeni konu
  python yazar.py space 5 --dry-run    # API'ye gitmeden ne gönderileceğini göster
  python yazar.py space --no-verify    # web aramalı doğrulamayı atla (daha ucuz, daha riskli)
  python yazar.py --harcama        # bu ayın harcamasını göster

Anahtar: yanındaki anthropic_key.txt (ya da ANTHROPIC_API_KEY ortam değişkeni; bunu sistem geneline koyma,
Claude Code o zaman aboneliği bırakıp API'den faturalar).

Para güvenliği: harcama yazar-harcama.json'a yazılır; AYLIK_TAVAN aşılınca ve bir koşu --budget'ı aşınca
yeni API çağrısı yapılmaz. Asıl kilit Console tarafında: ödeme yöntemi ekleme, kredi satın alma, auto-reload açma —
o zaman kredi bitince istekler sadece durur.
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pipeline  # noqa: E402  (validate_topics, find_niches, _key, hook_pattern)
import sims      # noqa: E402  (BODIES, TEMPLATES, countries, angular_deg, horizon_km)

RULES = HERE / "prompts" / "kurallar.md"
LEDGER = HERE / "yazar-harcama.json"
KEY_FILE = "anthropic_key.txt"

MODEL = "claude-sonnet-5-5"
AYLIK_TAVAN = 80.0          # USD; aylık API kredisi 100, pay bırakıyoruz
KOSU_BUTCESI = 3.0          # USD; tek koşu için varsayılan tavan (--budget ile değişir)
MAX_FIX_ROUNDS = 2
VERIFY_SEARCHES = 3         # konu başına en fazla web araması

# USD / milyon token. Kaynak: platform.claude.com/docs/en/about-claude/pricing (Kasım 2026). Değişirse güncelle.
PRICES = {
    "claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_w": 2.5, "cache_r": 0.10},
    "claude-haiku-5-5":  {"in": 0.10, "out": 0.50, "cache_w": 0.125, "cache_r": 0.01},
}
UNKNOWN_PRICE = {"in": 25.0, "out": 125.0, "cache_w": 31.25, "cache_r": 2.5}   # bilinmeyen model: kötümser tahmin
SEARCH_USD = 10.0 / 1000    # web araması başına

TOPIC_SCHEMA = {
    "type": "object",
    "properties": {
        "slug": {"type": "string"},
        "title": {"type": "string"},
        "script": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "visuals": {"type": "array", "items": {"type": "string"}},
        "sim": {"type": "object"},
        "highlight": {"type": "array", "items": {"type": "string"}},
        "description": {"type": "string"},
        "tags": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "string"},
                  "description": "Every claim or number not computed by the pipeline, each with its source."},
    },
    "required": ["slug", "title", "script", "keywords", "visuals", "highlight", "description", "tags", "facts"],
}
ADD_TOPICS_TOOL = {
    "name": "add_topics",
    "description": "Return the new topics.json entries for this batch.",
    "input_schema": {
        "type": "object",
        "properties": {
            "topics": {"type": "array", "items": TOPIC_SCHEMA},
            "skipped_ideas": {"type": "array", "items": {"type": "string"},
                              "description": "Good ideas that need a template, body or country that does not exist."},
        },
        "required": ["topics"],
    },
}
VERDICT_TOOL = {
    "name": "verdict",
    "description": "Your fact-check result for one topic.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean", "description": "true if every claim and number in the script is correct"},
            "problems": {"type": "array", "items": {"type": "string"}},
            "sources": {"type": "array", "items": {"type": "string"}, "description": "URLs or source names you used"},
            "fixed_script": {"type": ["string", "null"],
                             "description": "Corrected script if a number was wrong and a small fix saves the topic; else null"},
            "fixed_title": {"type": ["string", "null"]},
        },
        "required": ["ok", "problems", "sources", "fixed_script", "fixed_title"],
    },
}
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": VERIFY_SEARCHES}


# ---------------------------------------------------------------- para

class Butce:
    """Her API çağrısının maliyetini usage'dan hesaplar, aylık deftere yazar, tavanları korur."""

    def __init__(self, run_cap, month_cap=AYLIK_TAVAN):
        self.run_cap = run_cap
        self.month_cap = month_cap
        self.run_usd = 0.0
        self.calls = 0
        self.searches = 0
        self.tokens = {"in": 0, "out": 0, "cache_w": 0, "cache_r": 0}
        self.month = dt.date.today().strftime("%Y-%m")
        self.ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
        self.month_usd = float(self.ledger.get(self.month, {}).get("usd", 0.0))

    def check(self, estimate=0.0):
        if self.run_usd + estimate > self.run_cap:
            raise BudgetError(f"koşu bütçesi doldu: {self.run_usd:.2f} + ~{estimate:.2f} > {self.run_cap:.2f} USD "
                              f"(--budget ile artırabilirsin)")
        if self.month_usd + self.run_usd + estimate > self.month_cap:
            raise BudgetError(f"aylık tavan doldu: {self.month_usd + self.run_usd:.2f} USD / {self.month_cap:.0f}")

    def add(self, model, usage):
        p = PRICES.get(model, UNKNOWN_PRICE)
        if model not in PRICES:
            print(f"  ! {model} fiyat tablosunda yok, kötümser tahminle sayıldı")
        g = lambda k: int(getattr(usage, k, 0) or 0)
        n = {"in": g("input_tokens"), "out": g("output_tokens"),
             "cache_w": g("cache_creation_input_tokens"), "cache_r": g("cache_read_input_tokens")}
        stu = getattr(usage, "server_tool_use", None)
        searches = int(getattr(stu, "web_search_requests", 0) or 0) if stu else 0
        usd = sum(n[k] / 1e6 * p[k] for k in n) + searches * SEARCH_USD
        for k in n:
            self.tokens[k] += n[k]
        self.searches += searches
        self.calls += 1
        self.run_usd += usd
        return usd

    def save(self):
        m = self.ledger.setdefault(self.month, {"usd": 0.0, "runs": 0})
        m["usd"] = round(float(m["usd"]) + self.run_usd, 4)
        m["runs"] = int(m["runs"]) + 1
        LEDGER.write_text(json.dumps(self.ledger, indent=2) + "\n", encoding="utf-8")

    def summary(self):
        t = self.tokens
        return (f"{self.calls} çağrı, {t['in'] + t['cache_w'] + t['cache_r']:,} giriş / {t['out']:,} çıkış token, "
                f"{self.searches} arama → bu koşu {self.run_usd:.3f} USD, bu ay toplam "
                f"{self.month_usd + self.run_usd:.2f} / {self.month_cap:.0f} USD")


class BudgetError(RuntimeError):
    pass


# ---------------------------------------------------------------- bağlam

def numbers_sheet():
    """Modelin sayıları uydurmaması için motorun ölçtüğü değerler: çaplar, hacim oranları, açısal boyutlar, ülkeler."""
    B = sims.BODIES
    lines = ["## Numbers sheet (computed from the engine's own data: NASA equatorial diameters, IAU Sun radius)", "",
             "Diameters (km): " + ", ".join(f"{b['name']} {b['d']:,}" for b in B.values()), "",
             "Moon distance from Earth (center to center, NASA Moon fact sheet): mean 384,400 km, closest (perigee) "
             "363,300 km, farthest (apogee) 405,500 km. Moon's orbit diameter at the mean distance: 768,800 km.", "",
             "compare template — count = volume ratio big/small (use this as `count` and as the 'fill' number), "
             "across = whole number of small bodies that fit across the big one's diameter:"]
    order = sorted(B, key=lambda k: B[k]["d"])
    for i, s in enumerate(order):
        for b in order[i + 1:]:
            ratio = (B[b]["d"] / B[s]["d"]) ** 3
            across = int(B[b]["d"] // B[s]["d"])
            lines.append(f"- {B[s]['name']} in {B[b]['name']}: count {ratio:,.0f}, across {across}")
    planets = ["mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune"]
    lines += ["", f"lineup — all seven planets stacked: {sum(B[p]['d'] for p in planets):,} km wide "
              f"(add the diameters above for other stacks).", "",
              "sky template — angular size seen from Earth at the Moon's mean distance (384,400 km), and how many "
              "times wider than our Moon (0.52 degrees):"]
    moon_deg = sims.angular_deg(B["moon"]["d"] / 2, 384400)
    for k in order:
        if k == "moon":
            continue
        deg = sims.angular_deg(B[k]["d"] / 2, 384400)
        lines.append(f"- {B[k]['name']}: {deg:.1f} degrees, {deg / moon_deg:.0f}x wider than the Moon")
    lines += ["", "collapse template — event horizon diameter = mass_suns × 5.906 km: Sun 5.9 km, Jupiter about 5.6 "
              "meters (mass 0.000955 suns), Saturn about 1.7 meters (0.000286), Earth about 1.8 centimeters "
              "(0.000003003), Moon about 0.2 millimeters (0.0000000369).", "",
              "engulf — the Moon's orbit (768,800 km across at the mean distance) next to the Sun (1,391,400 km).", ""]
    try:
        cs = sims.countries()
        lines.append("map template — countries available (outline width × height in km, Natural Earth):")
        lines += [f"- {k}: {c['name']} {c['width_km']:,.0f} × {c['height_km']:,.0f} km" for k, c in cs.items()]
    except Exception as e:  # geo/countries.json yoksa map şablonu kullanılamaz
        lines.append(f"map template — unavailable ({e}); do not use it.")
    return "\n".join(lines)


def hook_of(script):
    """İlk cümle (kanca); pipeline ile aynı cümle bölme kuralı, ondalık sayılar bölünmez."""
    toks = script.split()
    spans = pipeline.sentence_spans(script)
    return " ".join(toks[spans[0][0]:spans[0][1] + 1]) if spans else script


def existing_block(topics):
    rows = []
    for t in topics:
        hook = hook_of(t.get("script", ""))
        kind = (t.get("sim") or {}).get("type", "stock")
        rows.append(f"- {t.get('slug', '?')} [{kind}] hook: {hook} | title: {t.get('title', '')}")
    return "\n".join(rows)


def build_messages(niche, count):
    cfg = niche.cfg
    system = RULES.read_text(encoding="utf-8") + "\n\n" + numbers_sheet()
    user = (f"Channel: {cfg.get('channel', niche.name)} (niche folder `{niche.name}`). lang: {cfg.get('lang', 'en')}. "
            f"sim_only: {str(bool(cfg.get('sim_only'))).lower()}.\n"
            f"Templates and their steps: " +
            "; ".join(f"{k}: {', '.join(v)}" for k, v in sims.TEMPLATES.items()) + "\n\n"
            f"Existing topics ({len(niche.topics)}) — never repeat a topic, a core fact or a hook skeleton:\n"
            f"{existing_block(niche.topics)}\n\n"
            f"Write {count} new topics for this channel and return them with the add_topics tool.")
    return system, [{"role": "user", "content": user}]


# ---------------------------------------------------------------- API

def client_or_die():
    key = pipeline._key("ANTHROPIC_API_KEY", KEY_FILE)
    if not key:
        sys.exit(f"API anahtarı yok: {KEY_FILE} dosyasına yapıştır (platform.claude.com → API keys).")
    try:
        import anthropic
    except ImportError:
        sys.exit("anthropic paketi yok: venv içinde  python -m pip install anthropic")
    return anthropic.Anthropic(api_key=key)


def call(client, butce, model, system, messages, tools, max_tokens, estimate):
    butce.check(estimate)
    kwargs = dict(model=model, max_tokens=max_tokens, messages=messages, tools=tools)
    if system:
        kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    resp = client.messages.create(**kwargs)
    usd = butce.add(model, resp.usage)
    print(f"  api: {model} stop={resp.stop_reason} {usd:.4f} USD")
    return resp


def tool_input(resp, name):
    for block in resp.content:
        if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == name:
            return block.input
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.S)       # tool çağrısı yoksa metindeki JSON'u dene
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------- denetim

def pattern_slugs(msg):
    """'aynı kanca kalıbı (...): a-b, c-d. YouTube...' uyarısındaki slug listesi."""
    m = re.search(r"\): (.+?)\. YouTube", msg)
    return {x.strip() for x in m.group(1).split(",")} if m else set()


def flagged_slugs(probs):
    out = set()
    for p in probs:
        if "kanca kalıbı" in p:
            out |= pattern_slugs(p)
        else:
            out.add(p.split(":")[0])
    return out


def problems_for(niche, kept, new):
    """validate_topics'i mevcut + tutulan + yeni liste üzerinde çalıştırır; yalnızca yeni konulara dokunan
    mesajları döndürür. Yeni konularda uyarılar da hata sayılır (kelime sayısı, kanca uzunluğu, başlık, kanca kalıbı)."""
    new_clean = [strip_facts(t)[0] for t in new]
    errors, warnings = pipeline.validate_topics(niche.topics + [strip_facts(t)[0] for t in kept] + new_clean)
    slugs = {t.get("slug") for t in new_clean}
    out = []
    for msg in errors + warnings:
        if "kanca kalıbı" in msg:
            if pattern_slugs(msg) & slugs:
                out.append(msg)
        elif msg.split(":")[0] in slugs:
            out.append(msg)
    if niche.cfg.get("sim_only"):
        out += [f"{t.get('slug')}: sim_only kanal; her visuals girdisi sim:<adım> olmalı ve sim nesnesi dolu olmalı"
                for t in new_clean if not pipeline.all_sim(t) or not t.get("sim")]
    return out


def strip_facts(t):
    t = dict(t)
    facts = t.pop("facts", [])
    return t, facts


# ---------------------------------------------------------------- akış

def generate(client, butce, args, niche):
    system, messages = build_messages(niche, args.count)
    est_in = (len(system) + len(messages[0]["content"])) / 4
    estimate = est_in / 1e6 * PRICES.get(args.model, UNKNOWN_PRICE)["in"] + 6000 / 1e6 * PRICES.get(args.model, UNKNOWN_PRICE)["out"]
    print(f"[1/4] {args.count} konu isteniyor (~{est_in:,.0f} giriş token)")
    resp = call(client, butce, args.model, system, messages, [ADD_TOPICS_TOOL], 16000, estimate)
    data = tool_input(resp, "add_topics")
    if not data or not data.get("topics"):
        sys.exit("Model add_topics çağırmadı; yanıt: " + "".join(getattr(b, "text", "") for b in resp.content)[:800])
    new = data["topics"]
    for idea in data.get("skipped_ideas") or []:
        print(f"  atlanan fikir (şablon yok): {idea}")

    for rnd in range(MAX_FIX_ROUNDS + 1):
        probs = problems_for(niche, [], new)
        print(f"[2/4] validate: {len(new)} konu, {len(probs)} sorun" + (f" (düzeltme turu {rnd})" if rnd else ""))
        for p in probs:
            print("   - " + p)
        if not probs or rnd == MAX_FIX_ROUNDS:
            break
        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content":
                         "The validator reported these problems:\n" + "\n".join(f"- {p}" for p in probs) +
                         "\n\nCall add_topics again with the COMPLETE batch: fix only the flagged topics (or replace a "
                         "topic you cannot fix with a new one that passes), keep the others exactly as they were."})
        resp = call(client, butce, args.model, system, messages, [ADD_TOPICS_TOOL], 16000, estimate)
        data = tool_input(resp, "add_topics")
        if not data or not data.get("topics"):
            break
        new = data["topics"]

    if probs:   # hâlâ sorunlu olan konuları at
        bad = flagged_slugs(probs) & {t["slug"] for t in new}
        kept = [t for t in new if t["slug"] not in bad]
        print(f"  düzelmeyen {len(new) - len(kept)} konu atıldı: {', '.join(sorted(bad))}")
        new = kept
    return new


def verify(client, butce, args, niche, new):
    print(f"[3/4] doğrulama: {len(new)} konu, konu başına en fazla {VERIFY_SEARCHES} web araması")
    kept, notes = [], {}
    p = PRICES.get(args.verify_model, UNKNOWN_PRICE)
    for t in new:
        facts = "\n".join(f"- {f}" for f in t.get("facts") or []) or "- (none listed)"
        prompt = (f"Fact-check this YouTube Shorts script. Numbers that come from the engine (planet diameters, volume "
                  f"ratios, angular sizes, event-horizon sizes, country widths) are already verified; check everything "
                  f"else: named sizes, masses, distances, times, records, and whether the overall claim is true as "
                  f"stated (a width ratio presented as a count or an area is wrong). Do not decide from memory: run at "
                  f"least one web search for every item in the writer's fact list, up to {VERIFY_SEARCHES} searches in "
                  f"total, and prefer space agencies, encyclopedias and university pages. If the fact list is empty "
                  f"and every number is engine-computed, one search confirming the main claim is enough. Then call the "
                  f"verdict tool with the URLs you used in `sources`.\n\n"
                  f"Title: {t['title']}\nScript: {t['script']}\nsim: {json.dumps(t.get('sim') or {})}\n"
                  f"Writer's fact list:\n{facts}")
        messages = [{"role": "user", "content": prompt}]
        estimate = 1500 / 1e6 * p["in"] + 1500 / 1e6 * p["out"] + VERIFY_SEARCHES * SEARCH_USD
        verdict = None
        try:
            for _ in range(4):   # pause_turn: arama uzun sürerse sunucu molası verir, devam et
                resp = call(client, butce, args.verify_model, None, messages, [WEB_SEARCH_TOOL, VERDICT_TOOL], 4000, estimate)
                verdict = tool_input(resp, "verdict")
                if verdict is not None or resp.stop_reason != "pause_turn":
                    break
                messages.append({"role": "assistant", "content": resp.content})
        except BudgetError as e:
            print(f"  ! {e}; kalan konular doğrulanmadan bırakıldı")
            notes[t["slug"]] = ["doğrulanmadı: bütçe"]
            kept.append(t)
            continue
        if verdict is None:
            print(f"  ? {t['slug']}: verdict gelmedi, doğrulanmamış sayıldı (tutuldu)")
            notes[t["slug"]] = ["doğrulanamadı"]
            kept.append(t)
            continue
        srcs = verdict.get("sources") or []
        if verdict.get("ok"):
            print(f"  ✓ {t['slug']}")
            notes[t["slug"]] = ["doğrulandı"] + srcs
            kept.append(t)
            continue
        probs = verdict.get("problems") or []
        fixed = verdict.get("fixed_script")
        if fixed:
            t2 = dict(t, script=fixed, title=verdict.get("fixed_title") or t["title"])
            if not problems_for(niche, kept, [t2]):
                print(f"  ~ {t['slug']}: düzeltildi ({'; '.join(probs)[:120]})")
                notes[t["slug"]] = ["düzeltildi: " + "; ".join(probs)] + srcs
                kept.append(t2)
                continue
        print(f"  ✗ {t['slug']}: atıldı ({'; '.join(probs)[:160]})")
    return kept, notes


def append_and_report(args, niche, new, notes, butce):
    path = niche.dir / "topics.json"
    backup = pipeline.CACHE / f"topics-{niche.name}-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    pipeline.CACHE.mkdir(exist_ok=True)
    shutil.copy2(path, backup)
    clean = [strip_facts(t)[0] for t in new]
    path.write_text(json.dumps(niche.topics + clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[4/4] {len(clean)} konu eklendi → {path.relative_to(HERE)} (yedek: {backup.relative_to(HERE)})")
    r = subprocess.run([sys.executable, str(HERE / "pipeline.py"), "--validate", niche.name], cwd=HERE,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONUTF8": "1"})
    print((r.stdout + r.stderr).strip())
    if r.returncode:
        print("  ! --validate hata verdi; yedekten geri alıyorum")
        shutil.copy2(backup, path)
        sys.exit(1)

    out = pipeline.OUT_ROOT / niche.name
    out.mkdir(parents=True, exist_ok=True)
    report = out / f"yazar-{dt.datetime.now():%Y%m%d-%H%M}.md"
    lines = [f"# yazar.py — {niche.name} — {dt.datetime.now():%Y-%m-%d %H:%M}", "",
             f"Model: {args.model}" + ("" if args.no_verify else f", doğrulama: {args.verify_model}"), f"Maliyet: {butce.summary()}", ""]
    for t in new:
        hook = hook_of(t["script"])
        lines += [f"## {t['slug']}", f"- Kanca: {hook}", f"- Şablon: {(t.get('sim') or {}).get('type', 'stock')}"]
        lines += [f"- {n}" for n in notes.get(t["slug"], [])]
        lines += [f"- Yazarın kaynakları: {f}" for f in t.get("facts") or []]
        lines.append("")
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"rapor: {report.relative_to(HERE)}")
    print(f"Render: calistir.bat {niche.name}   (ya da python pipeline.py {niche.name})")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Claude API ile yeni Shorts senaryoları üretir ve topics.json'a ekler.")
    ap.add_argument("niche", nargs="?", help="niches/ altındaki klasör (örn. space)")
    ap.add_argument("count", nargs="?", type=int, default=10, help="kaç yeni konu (1-20, varsayılan 10)")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--verify-model", default=None, help="doğrulama modeli (varsayılan: --model)")
    ap.add_argument("--no-verify", action="store_true", help="web aramalı doğrulamayı atla")
    ap.add_argument("--dry-run", action="store_true", help="API'ye gitme; gönderilecek bağlamı ve tahmini göster")
    ap.add_argument("--yes", "-y", action="store_true", help="eklemeden önce sorma")
    ap.add_argument("--budget", type=float, default=KOSU_BUTCESI, help=f"bu koşu için USD tavanı (varsayılan {KOSU_BUTCESI})")
    ap.add_argument("--aylik-tavan", type=float, default=AYLIK_TAVAN,
                    help=f"bu ay toplam harcama bu USD'yi geçince çağrı yapma (varsayılan {AYLIK_TAVAN:.0f})")
    ap.add_argument("--harcama", action="store_true", help="aylık harcama defterini göster ve çık")
    args = ap.parse_args()
    args.verify_model = args.verify_model or args.model

    if args.harcama:
        b = Butce(args.budget, args.aylik_tavan)
        print(json.dumps(b.ledger, indent=2) if b.ledger else "henüz harcama yok")
        print(f"bu ay: {b.month_usd:.2f} / {b.month_cap:.0f} USD")
        return
    if not args.niche:
        ap.error("niş klasörü gerekli (örn. space)")
    if not 1 <= args.count <= 20:
        ap.error("count 1-20 arasında olmalı")
    if not RULES.exists():
        sys.exit(f"{RULES.relative_to(HERE)} yok")

    niche = pipeline.find_niches([args.niche])[0]
    butce = Butce(args.budget, args.aylik_tavan)
    print(f"{niche.cfg.get('channel', niche.name)}: {len(niche.topics)} mevcut konu; bu ay {butce.month_usd:.2f} USD harcandı")

    if args.dry_run:
        system, messages = build_messages(niche, args.count)
        p = PRICES.get(args.model, UNKNOWN_PRICE)
        tok = (len(system) + len(messages[0]["content"])) / 4
        est = tok / 1e6 * p["in"] + 6000 / 1e6 * p["out"]
        ver = 0 if args.no_verify else args.count * (3000 / 1e6 * p["in"] + 1500 / 1e6 * p["out"] + VERIFY_SEARCHES * SEARCH_USD)
        print(f"\n--- system prompt ({len(system):,} karakter) ---\n{system[:1500]}\n...\n")
        print(f"--- user message ({len(messages[0]['content']):,} karakter) ---\n{messages[0]['content'][:2500]}\n...\n")
        print(f"tahmin: ~{tok:,.0f} giriş token; üretim ~{est:.2f} USD + doğrulama ~{ver:.2f} USD = ~{est + ver:.2f} USD "
              f"(ilk çağrıdan sonra system prompt önbellekten okunur, daha ucuz)")
        return

    client = client_or_die()
    try:
        new = generate(client, butce, args, niche)
        if not new:
            sys.exit("Eklenecek sağlam konu kalmadı.")
        notes = {}
        if not args.no_verify:
            new, notes = verify(client, butce, args, niche, new)
    except BudgetError as e:
        sys.exit(f"DURDU: {e}\n{butce.summary()}")
    except Exception as e:   # API/ağ hatası: iz yerine kısa mesaj
        sys.exit(f"HATA ({type(e).__name__}): {e}\n{butce.summary()}")
    finally:
        butce.save()          # ne olursa olsun harcama deftere yazılır
    print(f"\n{butce.summary()}\n")
    if not new:
        sys.exit("Doğrulamadan geçen konu kalmadı.")
    for t in new:
        hook = hook_of(t["script"])
        print(f"  • {t['slug']} [{(t.get('sim') or {}).get('type', 'stock')}]: {hook}")
    if not args.yes:
        ans = input(f"\n{len(new)} konu topics.json'a eklensin mi? [E/h] ").strip().lower()
        if ans not in ("", "e", "evet", "y", "yes"):
            dump = pipeline.CACHE / f"yazar-bekleyen-{niche.name}.json"
            pipeline.CACHE.mkdir(exist_ok=True)
            dump.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"eklenmedi; konular {dump.relative_to(HERE)} dosyasında duruyor")
            return
    append_and_report(args, niche, new, notes, butce)


if __name__ == "__main__":
    main()
