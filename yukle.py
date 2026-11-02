"""
yukle.py — render edilmiş videoları YouTube Data API ile yükler ve program.json'daki saate planlar.

Her niş görünümü (output/<niş>/ ve output/<niş>-sim/) için program.json'daki sırayı okur, henüz yüklenmemiş
.mp4'leri bulur, başlık/açıklama/etiketleri topics.json'dan alır, videoyu "private + publishAt" olarak yükler
(YouTube o saatte kendisi yayınlar) ve output/<görünüm>/yuklendi.json'a video id'sini yazar.

Kullanım (venv açıkken, repo kökünde):
  python yukle.py space --dry-run        # ne yüklenecek, hangi saate; API'ye gitmez
  python yukle.py space --test <slug>    # TEK videoyu "unlisted" yükler: kanal denetimsiz API'de kilitleniyor mu, görürsün
  python yukle.py space                  # sıradaki en fazla 14 videoyu yükle ve planla
  python yukle.py space --max 4
  python yukle.py space --include-past   # saati geçmiş (normalde elle yüklenmiş sayılan) videoları da yükle

Kurulum (bir kere):
  1. console.cloud.google.com → proje → "YouTube Data API v3" etkinleştir.
  2. OAuth consent screen: External, uygulama adı, destek e-postası; "Publish app" (Testing'de kalırsa token 7 günde ölür).
  3. Credentials → OAuth client ID → Desktop app → JSON indir → bu klasöre client_secret.json adıyla koy (git-ignored).
  4. python -m pip install google-api-python-client google-auth-oauthlib
  5. İlk çalıştırmada tarayıcı açılır, kanal hesabınla izin ver; youtube_token.json yazılır (git-ignored).

Kota: videos.insert günde 100 çağrı (varsayılan). Günde 2 video için bol.
Denetimsiz API projesinden yüklenen videoların "private" kilitlenip kilitlenmediği --test ile anlaşılır; kilitleniyorsa
YouTube API Services Audit formu doldurulur (bu script "çalışan yükleme script'i" olarak verilir).
"""
import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pipeline  # noqa: E402

CLIENT_SECRET = HERE / "client_secret.json"
TOKEN = HERE / "youtube_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]   # yalnızca yükleme; privacy.html bunu vaat ediyor
MAX_PER_RUN = 14
GRACE_MIN = 15          # saati geçmiş bir slot: şimdi + 15 dk'ya planlanır
CHUNK = 8 * 1024 * 1024


# ---------------------------------------------------------------- yetki

def service():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        sys.exit("Google paketleri yok: venv içinde  python -m pip install google-api-python-client google-auth-oauthlib")
    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            print(f"token yenilenemedi ({e}); yeniden giriş gerekiyor")
            creds = None
    if not creds or not creds.valid:
        if not CLIENT_SECRET.exists():
            sys.exit(f"{CLIENT_SECRET.name} yok: Google Cloud → Credentials → OAuth client (Desktop) → JSON indir, buraya koy.")
        if not sys.stdin or not sys.stdin.isatty():
            sys.exit("YouTube girişi gerekiyor ama bu otomatik bir koşu; bir kere elle  python yukle.py space --dry-run  çalıştır.")
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
        print(f"giriş tamam → {TOKEN.name}")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------- plan

def views(niche_name):
    """Bir nişin görünümleri: output/<niş> (stok) ve output/<niş>-sim (kodla çizilen); ikisi de olmayabilir."""
    niche = pipeline.find_niches([niche_name])[0]
    out = []
    for v in pipeline.split_outputs([niche]):
        if (v.out / "program.json").exists():
            out.append(v)
    return niche, out


def ledger_path(view):
    return view.out / "yuklendi.json"


def load_ledger(view):
    p = ledger_path(view)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def pending(view, ledger, now=None, include_past=False):
    """Yüklenecekler: program.json'da saati GELECEKTE olan, mp4'ü hazır, deftere girmemiş videolar; saate göre.
    Saati geçmiş olanlar elle yüklenmiş sayılır ve atlanır (--include-past: şimdi + 15 dk'ya planlanıp yüklenir)."""
    now = now or dt.datetime.now()
    prog = json.loads((view.out / "program.json").read_text(encoding="utf-8"))
    by_slug = {t["slug"]: t for t in view.topics}
    items, skipped = [], 0
    for slug, when in sorted(prog.items(), key=lambda kv: kv[1]):
        if slug in ledger or slug not in by_slug:
            continue
        mp4 = view.out / f"{slug}.mp4"
        if not mp4.exists():
            continue
        slot = dt.datetime.fromisoformat(when)
        if slot <= now:
            if not include_past:
                skipped += 1
                continue
            slot = now + dt.timedelta(minutes=GRACE_MIN)
        items.append((slug, by_slug[slug], mp4, slot))
    if skipped:
        print(f"  ({view.name}: saati geçmiş {skipped} video elle yüklenmiş sayıldı, atlandı; --include-past ile dahil edilir)")
    return items


def body_for(view, t, slot, privacy="private"):
    cfg = view.cfg
    tags = [x.strip() for x in pipeline.youtube_tags(t, cfg).split(",") if x.strip()]
    desc = (t.get("description", "") + "\n\n" + t.get("tags", cfg.get("default_tags", ""))).strip()
    body = {
        "snippet": {"title": t["title"][:100], "description": desc[:4900], "tags": tags,
                    "categoryId": str(cfg.get("youtube_category", 28)),      # 28: Science & Technology
                    "defaultLanguage": cfg.get("lang", "en"), "defaultAudioLanguage": cfg.get("lang", "en")},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    if slot is not None:
        body["status"]["publishAt"] = slot.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return body


# ---------------------------------------------------------------- yükleme

def upload(yt, mp4, body):
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(str(mp4), chunksize=CHUNK, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp, tries = None, 0
    while resp is None:
        try:
            status, resp = req.next_chunk()
            if status:
                print(f"    {int(status.progress() * 100)}%", end="\r")
        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504) and tries < 5:
                tries += 1
                time.sleep(2 ** tries)
                continue
            raise
    return resp


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Render edilmiş videoları YouTube'a yükler ve program.json'daki saate planlar.")
    ap.add_argument("niche", help="niches/ altındaki klasör (örn. space)")
    ap.add_argument("--max", type=int, default=MAX_PER_RUN, help=f"bu koşuda en fazla kaç video (varsayılan {MAX_PER_RUN})")
    ap.add_argument("--dry-run", action="store_true", help="API'ye gitme; listeyi göster")
    ap.add_argument("--test", metavar="SLUG", help="tek videoyu 'unlisted' yükle (plan yok): kilit testi")
    ap.add_argument("--include-past", action="store_true", help="saati geçmiş videoları da yükle (şimdi + 15 dk'ya planlanır)")
    args = ap.parse_args()

    niche, vs = views(args.niche)
    if not vs:
        sys.exit("program.json yok: önce render et (calistir.bat %s)." % args.niche)

    plan = []
    for v in vs:
        ledger = load_ledger(v)
        for slug, t, mp4, slot in pending(v, ledger, include_past=args.include_past):
            plan.append((v, slug, t, mp4, slot))
    plan.sort(key=lambda x: x[4])

    if args.test:
        hit = [p for p in plan if p[1] == args.test] or \
              [(v, args.test, next((t for t in v.topics if t["slug"] == args.test), None), v.out / f"{args.test}.mp4", None)
               for v in vs if (v.out / f"{args.test}.mp4").exists()]
        if not hit or hit[0][2] is None:
            sys.exit(f"{args.test}.mp4 bulunamadı ya da topics.json'da yok")
        v, slug, t, mp4, _ = hit[0]
        body = body_for(v, t, None, privacy="unlisted")
        print(f"TEST: {mp4.name} 'unlisted' olarak yükleniyor (planlama yok)")
        if args.dry_run:
            print(json.dumps(body, indent=2, ensure_ascii=False))
            return
        yt = service()
        resp = upload(yt, mp4, body)
        vid = resp["id"]
        st = resp.get("status", {})
        print(f"video: https://youtu.be/{vid}\n  istenen: unlisted  →  YouTube yanıtı: {st.get('privacyStatus')} "
              f"(uploadStatus {st.get('uploadStatus')})")
        print("  Şimdi YouTube Studio → İçerik'te bu videoya bak:\n"
              "  - 'Liste dışı' görünüyorsa KİLİT YOK → otomatik yükleme serbest; test videosunu silebilirsin.\n"
              "  - 'Gizli' + kilit simgesi/'unverified API' uyarısı varsa KİLİT VAR → Audit formunu doldur\n"
              "    (bu script + ekran kaydı); geçene kadar youtube_token.json'u sil, otomatik.bat yüklemeyi atlar.")
        return

    plan = plan[: args.max]
    if not plan:
        print("yüklenecek yeni video yok")
        return
    print(f"{len(plan)} video:")
    for v, slug, t, mp4, slot in plan:
        print(f"  {slot:%a %d.%m %H:%M}  {v.name}/{slug}.mp4  |  {t['title']}")
    if args.dry_run:
        return

    yt = service()
    done = 0
    for v, slug, t, mp4, slot in plan:
        body = body_for(v, t, slot)
        print(f"→ {slug} ({slot:%d.%m %H:%M})")
        try:
            resp = upload(yt, mp4, body)
        except Exception as e:
            print(f"  ! yükleme hatası: {e}")
            if "quota" in str(e).lower():
                break
            continue
        vid = resp["id"]
        ledger = load_ledger(v)
        ledger[slug] = {"video_id": vid, "publish_at": slot.isoformat(timespec="minutes"),
                        "uploaded_at": dt.datetime.now().isoformat(timespec="minutes")}
        ledger_path(v).write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        st = resp.get("status", {})
        print(f"  ✓ https://youtu.be/{vid}  (privacy {st.get('privacyStatus')}, publishAt {body['status']['publishAt']})")
        done += 1
    print(f"\n{done}/{len(plan)} video yüklendi ve planlandı.")


if __name__ == "__main__":
    main()
