/* Shorts Fabrikası — web app. Reads the kit straight from this repository.
   Runs as a plain website (GitHub Pages: your own Claude API key, normal download)
   or inside a Claude artifact (the viewer's Claude account, Claude's download dialog). */
const KIT_FILES = ["pipeline.py", "kurulum.bat", "calistir.bat", "OKU-BENI.txt", "README.md",
                   ".gitignore", ".gitattributes", "muzik/BURAYA-MUZIK-AT.txt"];
const OPTIONAL = new Set([".gitignore", ".gitattributes", "README.md"]);
const VOICES = {
  en: [["en-US-BrianNeural","Brian · samimi"],["en-US-AndrewNeural","Andrew · sıcak"],["en-US-GuyNeural","Guy · enerjik"],["en-US-ChristopherNeural","Christopher · belgesel"],["en-GB-RyanNeural","Ryan · İngiliz"],["en-US-AriaNeural","Aria · kadın, canlı"],["en-US-JennyNeural","Jenny · kadın, sıcak"]],
  tr: [["tr-TR-AhmetNeural","Ahmet · erkek"],["tr-TR-EmelNeural","Emel · kadın"]],
};
const KEY_STORE = "asg_api_key";
const $ = (id) => document.getElementById(id);
const inClaude = !!(window.claude && window.claude.use);
let PRESETS = {}, picked = new Set(["space"]), custom = null, ctl = null, sample = null, downloads = null;

const crlf = (s) => s.replace(/\r?\n/g, "\r\n");          // .bat dosyaları Windows satır sonu ister
const toCss = (c) => "#" + String(c).replace(/^0x/, "");
const firstSentence = (s) => s.split(/(?<=[.?!])\s/)[0];
async function getText(path) {
  const r = await fetch(path, { cache: "no-cache" });
  if (!r.ok) throw new Error(path + " " + r.status);
  return r.text();
}

/* ---------- live caption in the hero ---------- */
(() => {
  const words = [["Paper",1],["can",0],["reach",0],["the",0],["Moon",1],["Just",0],["fold",0],["it",0],["42",1],["times",0]];
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const cap = $("cap"); let i = 0;
  setInterval(() => { i = (i + 1) % words.length; cap.textContent = words[i][0]; cap.classList.toggle("y", !!words[i][1]); }, 430);
})();

/* ---------- channel covers ---------- */
function captionNodes(text, highlights, color) {
  const hl = new Set(highlights.map((h) => h.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "")));
  const frag = document.createDocumentFragment();
  text.split(/\s+/).forEach((w, i) => {
    const s = document.createElement("span");
    s.textContent = (i ? " " : "") + w;
    const key = w.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
    if (/\d/.test(w) || hl.has(key)) s.style.color = color;
    frag.append(s);
  });
  return frag;
}

function cover({ id, title, sub, settings, topic, count }) {
  const b = document.createElement("button");
  b.type = "button"; b.className = "cover"; b.setAttribute("aria-pressed", picked.has(id) ? "true" : "false");
  const art = document.createElement("div"); art.className = "cover-art" + (settings.starfield ? " stars" : "");
  const pal = (settings.palettes && settings.palettes[0]) || ["0x111111", "0x333333", "0x000000"];
  art.style.background = `linear-gradient(165deg, ${toCss(pal[1])}, ${toCss(pal[0])} 55%, ${toCss(pal[2])})`;
  const cap = document.createElement("div"); cap.className = "caption"; cap.lang = settings.lang || "en";
  const hookLen = firstSentence(topic.script).length, longest = Math.max(...firstSentence(topic.script).split(/\s+/).map((w) => w.length));
  cap.style.setProperty("--fs", (hookLen <= 26 && longest <= 9 ? 1.6 : hookLen <= 44 && longest <= 11 ? 1.32 : 1.1) + "rem");
  cap.append(captionNodes(firstSentence(topic.script), topic.highlight || [], settings.highlight_color || "#FFD60A"));
  const tag = document.createElement("span"); tag.className = "tag"; tag.textContent = "Seçildi";
  const cnt = document.createElement("span"); cnt.className = "count"; cnt.textContent = `${count} video`;
  art.append(cap, tag, cnt);
  const meta = document.createElement("span"); meta.className = "cover-meta";
  const t = document.createElement("b"); t.textContent = title;
  const s = document.createElement("span"); s.textContent = sub;
  meta.append(t, s);
  b.append(art, meta);
  b.addEventListener("click", () => { picked.has(id) ? picked.delete(id) : picked.add(id); renderCovers(); renderSummary(); });
  return b;
}

function renderCovers() {
  const box = $("covers"); box.replaceChildren();
  for (const [key, p] of Object.entries(PRESETS))
    box.append(cover({ id: key, title: p.channel, sub: `${p.label} · ${p.voiceLabel}`, settings: p.settings, topic: p.topics[0], count: p.topics.length }));
  if (custom)
    box.append(cover({ id: "custom", title: custom.channel, sub: "Senin nişin", settings: custom.settings, topic: custom.topics[0], count: custom.topics.length }));
  const add = document.createElement("button"); add.type = "button"; add.className = "cover new";
  const art = document.createElement("div"); art.className = "cover-art";
  const plus = document.createElement("div"); plus.className = "plus"; plus.textContent = "+";
  const p = document.createElement("p"); p.textContent = custom ? "Yeniden yaz" : "Kendi nişin";
  art.append(plus, p);
  const meta = document.createElement("span"); meta.className = "cover-meta";
  const mt = document.createElement("b"); mt.textContent = "Kendi nişin"; const ms = document.createElement("span"); ms.textContent = "Claude yazar";
  meta.append(mt, ms); add.append(art, meta);
  add.addEventListener("click", () => { $("own").scrollIntoView({ behavior: "smooth", block: "start" }); setTimeout(() => $("niche").focus(), 350); });
  box.append(add);
}

async function loadPresets() {
  try {
    const index = JSON.parse(await getText("niches/index.json"));
    const loaded = await Promise.all(index.map(async (n) => {
      const [s, t] = await Promise.all([getText(`niches/${n.folder}/settings.json`), getText(`niches/${n.folder}/topics.json`)]);
      const settings = JSON.parse(s), topics = JSON.parse(t);
      topics.forEach((x) => { delete x.music; delete x.music_start; });     // müzik dosyaları pakette yok
      return [n.folder, { label: n.label, channel: settings.channel || n.folder,
        voiceLabel: (settings.voice || "").replace(/^\w\w-\w\w-|Neural$/g, ""), settings, topics }];
    }));
    PRESETS = Object.fromEntries(loaded);
    renderCovers(); renderSummary();
  } catch (e) {
    $("covers").innerHTML = '<p class="status warn">Kanallar yüklenemedi. Sayfayı bir sunucu üzerinden aç (GitHub Pages ya da <code>python -m http.server</code>).</p>';
  }
}

/* ---------- custom niche ---------- */
function fillVoices() {
  $("voice").replaceChildren(...VOICES[$("lang").value].map(([v, l]) => { const o = document.createElement("option"); o.value = v; o.textContent = l; return o; }));
}
$("lang").addEventListener("change", fillVoices);
try { const k = localStorage.getItem(KEY_STORE); if (k) { $("apikey").value = k; $("remember").checked = true; } } catch {}
$("remember").addEventListener("change", () => { try { if (!$("remember").checked) localStorage.removeItem(KEY_STORE); } catch {} });

const slugify = (s) => (s || "").toLowerCase()
  .replace(/ı/g, "i").replace(/ğ/g, "g").replace(/ü/g, "u").replace(/ş/g, "s").replace(/ö/g, "o").replace(/ç/g, "c")
  .normalize("NFKD").replace(/[^\w\s-]/g, "").trim().replace(/[\s_]+/g, "-").replace(/-+/g, "-").slice(0, 40);
const HEX = /^#[0-9a-fA-F]{6}$/, HEX0 = /^0x[0-9a-fA-F]{6}$/;

function buildPrompt(niche, lang, n) {
  const L = lang === "tr" ? "Turkish" : "English";
  return `You are writing scripts for an automated YouTube Shorts channel about: "${niche}".
Write ${n} short videos. Script language: ${L}. Reply with ONLY one JSON object, no other text.

Rules for every script:
- 32 to 42 words, read aloud by a text-to-speech voice in about 15 seconds.
- The FIRST sentence is a hook of at most 7 words: a shocking, surprising claim or question that makes the viewer think "no way" within 1.5 seconds. Example: "Paper can reach the Moon."
- Then explain it with concrete numbers or details, conversational, talking to the viewer ("you"). End with a twist or a short punchy line.
- Use ONLY well-established facts you are confident are true. No myths, no rumors, no speculation, no medical, legal or investment advice. Numbers must be accurate.
- Plain text only: no emojis, no hashtags, no quotes inside the script.

JSON shape:
{
  "folder": "short-english-kebab-case-name-for-this-niche",
  "fallback_queries": ["3 generic English stock-footage searches that fit the whole niche"],
  "default_tags": "#tag1 #tag2 #shorts",
  "highlight_color": "#RRGGBB bright caption highlight color that fits the niche",
  "palettes": [["0xRRGGBB","0xRRGGBB","0xRRGGBB"], ["0xRRGGBB","0xRRGGBB","0xRRGGBB"], ["0xRRGGBB","0xRRGGBB","0xRRGGBB"]],
  "starfield": false,
  "topics": [
    {
      "slug": "unique-english-kebab-case",
      "title": "catchy video title in ${L}, under 70 characters",
      "script": "the full script in ${L}",
      "keywords": ["English stock footage search 1-2 words", "a second, different 1-2 word search"],
      "highlight": ["2 to 4 key words copied exactly from the script"],
      "description": "one sentence in ${L}",
      "tags": "#niche #topic #shorts"
    }
  ]
}
Palettes are dark background gradient colors (mostly dark tones) matching the niche mood. Set "starfield" to true only for space or night-sky themes.`;
}

function parseJSON(text) {
  try { return JSON.parse(text); } catch {}
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fence) { try { return JSON.parse(fence[1]); } catch {} }
  const a = text.indexOf("{"), b = text.lastIndexOf("}");
  if (a >= 0 && b > a) { try { return JSON.parse(text.slice(a, b + 1)); } catch {} }
  throw { code: "invalid_json" };
}

function clean(raw, nicheText, lang, voice, channelIn) {
  if (!raw || !Array.isArray(raw.topics)) throw { code: "invalid_json" };
  const seen = new Set();
  const topics = raw.topics.filter((t) => t && typeof t.script === "string" && t.script.trim()).map((t, i) => {
    let slug = slugify(t.slug || t.title || "video-" + (i + 1)) || "video-" + (i + 1);
    while (seen.has(slug)) slug += "-2";
    seen.add(slug);
    const kws = (Array.isArray(t.keywords) ? t.keywords : [t.keywords]).map(String).map((s) => s.trim()).filter(Boolean).slice(0, 3);
    return { slug, title: String(t.title || "").slice(0, 100) || slug, script: t.script.trim(),
      keywords: kws.length ? kws : ["abstract background"],
      highlight: (Array.isArray(t.highlight) ? t.highlight : []).map(String).slice(0, 5),
      description: String(t.description || ""), tags: String(t.tags || raw.default_tags || "#shorts") };
  });
  if (!topics.length) throw { code: "invalid_json" };
  const palettes = (Array.isArray(raw.palettes) ? raw.palettes : []).filter((p) => Array.isArray(p) && p.length === 3 && p.every((c) => HEX0.test(c)));
  let folder = slugify(raw.folder) || slugify(nicheText) || "my-niche";
  if (PRESETS[folder]) folder += "-2";
  const channel = channelIn || nicheText;
  const fq = (Array.isArray(raw.fallback_queries) ? raw.fallback_queries : []).map(String).filter(Boolean).slice(0, 4);
  return { folder, channel, topics, settings: {
    channel, voice, lang, rate: "+8%", scene_seconds: 3.2, first_scene_seconds: 1.5,
    fallback_queries: fq.length ? fq : ["abstract background"],
    default_tags: String(raw.default_tags || "#shorts"),
    highlight_color: HEX.test(raw.highlight_color) ? raw.highlight_color : "#FFD60A",
    starfield: !!raw.starfield,
    palettes: palettes.length ? palettes : [["0x0f0c29","0x302b63","0x24243e"],["0x141e30","0x243b55","0x0b0b13"]],
    music: true, music_rel_db: -10, music_fade_in: 0, sfx: true, sfx_rel_db: -8 } };
}

function renderTopics() {
  const box = $("topics"); box.replaceChildren();
  if (!custom) return;
  custom.topics.forEach((t, i) => {
    const row = document.createElement("div"); row.className = "topic";
    const body = document.createElement("div");
    const parts = t.script.split(/(?<=[.?!])\s/);
    const hook = document.createElement("div"); hook.className = "hook"; hook.textContent = parts[0]; hook.lang = custom.settings.lang;
    const rest = document.createElement("div"); rest.className = "rest"; rest.textContent = parts.slice(1).join(" ");
    const kw = document.createElement("div"); kw.className = "kw"; kw.textContent = "görüntü: " + t.keywords.join(", ");
    body.append(hook, rest, kw);
    const x = document.createElement("button"); x.className = "x"; x.type = "button"; x.textContent = "✕"; x.setAttribute("aria-label", "Bu senaryoyu çıkar");
    x.addEventListener("click", () => { custom.topics.splice(i, 1); if (!custom.topics.length) { custom = null; picked.delete("custom"); } renderTopics(); renderCovers(); renderSummary(); });
    row.append(body, x); box.append(row);
  });
}

const ERR = {
  not_granted: "Bu sayfanın Claude kullanmasına izin verilmedi.",
  sampling_disabled: "Bu hesapta Claude'dan üretim kullanılamıyor.",
  rate_limited: "Çok fazla istek var ya da kullanım sınırına gelindi. Biraz sonra tekrar dene.",
  invalid_json: "Cevap beklenen biçimde gelmedi. Bir kez daha dene.",
  refused: "Bu konu için senaryo yazılamadı. Konuyu farklı ifade et.",
  session_expired: "Oturum süresi doldu, tekrar giriş yap.",
  401: "API anahtarı geçersiz. console.anthropic.com'dan yeni bir anahtar al.",
  credit: "API hesabında kredi yok. console.anthropic.com → Billing'den kredi ekle.",
  429: "Çok fazla istek. Bir dakika sonra tekrar dene.",
  busy: "Claude şu an yoğun. Biraz sonra tekrar dene.",
};

async function askClaude(prompt, signal) {
  if (inClaude) {
    if (!sample) throw { code: "not_granted" };
    return sample.json(prompt, { signal, cache: false });
  }
  const r = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST", signal,
    headers: { "content-type": "application/json", "x-api-key": $("apikey").value.trim(), "anthropic-version": "2023-06-01",
               "anthropic-dangerous-direct-browser-access": "true" },
    body: JSON.stringify({ model: $("model").value, max_tokens: 8000, messages: [{ role: "user", content: prompt }] }),
  });
  const data = await r.json().catch(() => null);
  if (!r.ok) {
    const msg = data?.error?.message || "";
    throw { code: r.status === 401 ? 401 : r.status === 429 ? 429 : r.status >= 500 ? "busy" : /credit/i.test(msg) ? "credit" : "other", message: msg };
  }
  return parseJSON((data.content || []).filter((c) => c.type === "text").map((c) => c.text).join(""));
}

$("genBtn").addEventListener("click", async () => {
  const st = $("genStatus"), niche = $("niche").value.trim();
  const say = (text, cls = "") => { st.className = "status " + cls; st.textContent = text; };
  if (!niche) { say("Önce bir niş ya da konu yaz.", "warn"); $("niche").focus(); return; }
  if (!inClaude && !$("apikey").value.trim()) { say("Claude API anahtarını gir.", "warn"); $("apikey").focus(); return; }
  try { if (!inClaude && $("remember").checked) localStorage.setItem(KEY_STORE, $("apikey").value.trim()); } catch {}
  ctl = new AbortController();
  $("genBtn").disabled = true; $("stopBtn").hidden = false;
  say("Senaryolar yazılıyor… yarım dakika kadar sürebilir.");
  try {
    const raw = await askClaude(buildPrompt(niche, $("lang").value, Number($("count").value)), ctl.signal);
    custom = clean(raw, niche, $("lang").value, $("voice").value, $("channel").value.trim());
    picked.add("custom");
    say(`${custom.topics.length} senaryo hazır, kanallara eklendi.`, "ok");
    renderTopics(); renderCovers(); renderSummary();
  } catch (e) {
    if (e?.name === "AbortError" || e?.code === "cancelled") say("Durduruldu.");
    else say(ERR[e?.code] || (e?.message ? "İstek başarısız oldu: " + e.message : "Bağlantı kurulamadı. İnternetini ve anahtarını kontrol et."), "warn");
  } finally { $("genBtn").disabled = false; $("stopBtn").hidden = true; }
});
$("stopBtn").addEventListener("click", () => ctl && ctl.abort());

/* ---------- download ---------- */
function selected() {
  const out = [];
  for (const key of Object.keys(PRESETS)) if (picked.has(key)) out.push({ folder: key, ...PRESETS[key] });
  if (custom && picked.has("custom")) out.push(custom);
  return out;
}
function renderSummary() {
  const ns = selected(), videos = ns.reduce((a, n) => a + n.topics.length, 0);
  $("dlSummary").textContent = ns.length ? `${ns.length} kanal · ${videos} video` : "Önce bir kanal seç";
  $("dlBtn").disabled = !ns.length;
}

const NOTE = "Bu nise ozel muzikleri (.mp3) buraya at.\r\nBos kalirsa ana klasordeki 'muzik' klasoru kullanilir.\r\n";
$("dlBtn").addEventListener("click", async () => {
  const ns = selected(), st = $("dlStatus");
  const say = (text, cls = "") => { st.className = "status " + cls; st.textContent = text; };
  if (!ns.length) return;
  if (typeof JSZip === "undefined") { say("ZIP aracı yüklenemedi. Sayfayı yenile.", "warn"); return; }
  say("Paket hazırlanıyor…");
  try {
    const zip = new JSZip(), root = zip.folder("shorts-fabrikasi");
    for (const path of KIT_FILES) {
      try { let text = await getText(path); if (/\.(bat|txt)$/.test(path)) text = crlf(text); root.file(path, text); }
      catch (e) { if (!OPTIONAL.has(path)) throw e; }
    }
    for (const n of ns) {
      root.file(`niches/${n.folder}/settings.json`, JSON.stringify(n.settings, null, 2) + "\n");
      root.file(`niches/${n.folder}/topics.json`, JSON.stringify(n.topics, null, 2) + "\n");
      root.file(`niches/${n.folder}/muzik/BURAYA-MUZIK-AT.txt`, NOTE);
    }
    const blob = await zip.generateAsync({ type: "blob", compression: "DEFLATE" });
    if (inClaude) {
      if (!downloads) { say("İndirme bu görünümde kullanılamıyor.", "warn"); return; }
      await downloads.save({ filename: "shorts-fabrikasi.zip", data: blob });
    } else {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "shorts-fabrikasi.zip";
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    }
    say("İndirildi. Ayıkla, kurulum.bat'e çift tıkla.", "ok");
  } catch (e) {
    say(e?.code === "declined" ? "İndirme iptal edildi." : e?.code === "rate_limited" ? "Bir indirme penceresi zaten açık." : "Paket hazırlanamadı. Tekrar dene.", "warn");
  }
});

/* ---------- boot ---------- */
fillVoices(); loadPresets();
if (inClaude) {
  $("keyField").hidden = true; $("modelField").hidden = true;
  $("aiNote").textContent = "Bu sayfa Claude içinde açık: senaryolar senin Claude hesabınla yazılır, ayrıca anahtar gerekmez.";
  (async () => {
    [sample, downloads] = await Promise.all([window.claude.use("sample"), window.claude.use("downloads")]);
    if (!sample) { $("genBtn").disabled = true; $("genStatus").textContent = "Claude bu görünümde kullanılamıyor."; }
  })();
}
