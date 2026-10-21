#!/usr/bin/env python3
"""
basari_skoru.py — Steam tür analizi için başarı skoru (ücretsiz kaynaklar)

Bir Steam etiketindeki (ör. "Roguelike Deckbuilder") indie oyunları toplar,
her birine 100 üzerinden bir başarı skoru verir ve sıralı CSV + JSON üretir.

Kaynaklar (hepsi ücretsiz):
  - SteamSpy API      : etiketteki oyun listesi, dünkü zirve eşzamanlı oyuncu
  - Steam mağaza API  : çıkış tarihi, fiyat, yorum özeti, son 30 gün yorumları
  - Steam Web API     : anlık oyuncu sayısı
  - YouTube Data API  : (opsiyonel) ortalama video izlenmesi, ücretsiz anahtar ister

Kurulum:
  pip install requests

Kullanım:
  python basari_skoru.py --tag "Roguelike Deckbuilder"
  python basari_skoru.py --tag "Cozy" --max-price 20 --top 100
  python basari_skoru.py --appids 2379780 1145360 646570
  YOUTUBE_API_KEY=... python basari_skoru.py --tag "Survivors-like"

Not: Steam istek sınırları nedeniyle 150 oyun ilk çalıştırmada ~15-25 dk sürer.
Tüm yanıtlar ./cache klasörüne yazılır; tekrar çalıştırmalar çok hızlıdır.
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
from collections import deque
from pathlib import Path

import requests

# ---------------------------------------------------------------- ayarlar ---
# Toplam 100. Bir metrik bir oyun için ölçülemezse ağırlığı diğerlerine dağıtılır.
WEIGHTS = {
    "yorum_hizi": 30,    # toplam yorum / süre^0.7
    "pozitif_oran": 15,  # Wilson alt sınırı
    "aktif_oyuncu": 20,  # dünkü zirve eşzamanlı oyuncu
    "gelir_hizi": 15,    # yorum x satış çarpanı x fiyat / süre^0.7
    "guncel_ivme": 10,   # son 30 gün yorum / aylık ortalama yorum
    "sosyal": 10,        # YouTube medyan izlenme (anahtar yoksa atlanır)
}
TIME_EXP = 0.7          # zaman çarpanı üssü (1 = düz bölme)
REVIEW_TO_SALES = 30    # yorum başına tahmini satış (kaba vekil, Gamalytic gelince kalkar)
CACHE_HOURS = 24

STEAMSPY = "https://steamspy.com/api.php"
STORE_DETAILS = "https://store.steampowered.com/api/appdetails"
STORE_REVIEWS = "https://store.steampowered.com/appreviews/{appid}"
CURRENT_PLAYERS = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
STORE_SEARCH = "https://store.steampowered.com/search/results/"
TAG_LIST = "https://store.steampowered.com/tagdata/populartags/english"
YT_SEARCH = "https://www.googleapis.com/youtube/v3/search"
YT_VIDEOS = "https://www.googleapis.com/youtube/v3/videos"


# ------------------------------------------------------------- HTTP + cache ---
class Fetcher:
    """HTTP + disk önbelleği. Her isteği ve hatayı tanı raporu için kaydeder."""

    def __init__(self, cache_dir="cache", cache_hours=CACHE_HOURS, secrets=None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.max_age = cache_hours * 3600
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "indie-genre-research/0.1"
        self.secrets = [x for x in (secrets or []) if x]
        self.log = deque(maxlen=300)   # son istekler
        self.errors = []               # hata listesi
        self.stats = {"istek": 0, "onbellek": 0, "hata": 0}

    def mask(self, text):
        text = str(text)
        for sec in self.secrets:
            text = text.replace(sec, "***")
        return text

    def _safe_params(self, params):
        return {k: ("***" if "key" in k.lower() else v) for k, v in (params or {}).items()}

    def record_error(self, kind, message, severity="uyarı", **extra):
        self.errors.append({
            "zaman": dt.datetime.now().strftime("%H:%M:%S"), "onem": severity,
            "tur": kind, "mesaj": self.mask(message)[:600], **extra,
        })

    def _path(self, url, params):
        raw = url + json.dumps(params or {}, sort_keys=True)
        return self.cache_dir / (hashlib.sha1(raw.encode()).hexdigest() + ".json")

    def get_json(self, url, params=None, delay=1.0):
        path = self._path(url, params)
        short = url.split("//")[-1][:70]
        if path.exists() and time.time() - path.stat().st_mtime < self.max_age:
            self.stats["onbellek"] += 1
            self.log.append({"zaman": dt.datetime.now().strftime("%H:%M:%S"), "url": short,
                             "durum": "önbellek", "ms": 0})
            return json.loads(path.read_text(encoding="utf-8"))

        last_err, attempts = None, []
        for attempt in range(4):
            t0 = time.time()
            try:
                self.stats["istek"] += 1
                r = self.s.get(url, params=params, timeout=30)
                ms = int((time.time() - t0) * 1000)
                attempts.append(f"{r.status_code}/{ms}ms")
                if r.status_code == 429 or r.status_code >= 500:
                    last_err = RuntimeError(f"HTTP {r.status_code}")
                    time.sleep(10 * (2 ** attempt))  # istek sınırı: geri çekil
                    continue
                r.raise_for_status()
                data = r.json()
                path.write_text(json.dumps(data), encoding="utf-8")
                self.log.append({"zaman": dt.datetime.now().strftime("%H:%M:%S"), "url": short,
                                 "durum": r.status_code, "ms": ms})
                time.sleep(delay)
                return data
            except (requests.RequestException, ValueError) as e:
                attempts.append(type(e).__name__)
                last_err = e
                time.sleep(2 * (2 ** attempt))
        self.stats["hata"] += 1
        self.log.append({"zaman": dt.datetime.now().strftime("%H:%M:%S"), "url": short,
                         "durum": "HATA", "ms": 0})
        raise RuntimeError(self.mask(
            f"İstek başarısız: {url} params={self._safe_params(params)} "
            f"hata={type(last_err).__name__}: {last_err} denemeler={attempts}"))


# ----------------------------------------------------------- veri toplama ---
def steamspy_tag(f, tag):
    return f.get_json(STEAMSPY, {"request": "tag", "tag": tag}, delay=2)


def steamspy_app(f, appid):
    return f.get_json(STEAMSPY, {"request": "appdetails", "appid": appid}, delay=1)


def store_details(f, appid):
    d = f.get_json(STORE_DETAILS, {"appids": appid, "cc": "us", "l": "english"}, delay=1.5)
    entry = (d or {}).get(str(appid), {})
    return entry.get("data") if entry.get("success") else None


def review_summary(f, appid):
    d = f.get_json(STORE_REVIEWS.format(appid=appid), {
        "json": 1, "language": "all", "purchase_type": "all",
        "filter": "all", "num_per_page": 0,
    }, delay=1.0)
    return d.get("query_summary", {}) if d else {}


def recent_review_count(f, appid, days=30, max_pages=5):
    """Son `days` gündeki yorum sayısı. max_pages dolarsa alt sınırdır."""
    cutoff = time.time() - days * 86400
    cursor, count = "*", 0
    for _ in range(max_pages):
        d = f.get_json(STORE_REVIEWS.format(appid=appid), {
            "json": 1, "language": "all", "purchase_type": "all",
            "filter": "recent", "num_per_page": 100, "cursor": cursor,
        }, delay=1.0)
        reviews = (d or {}).get("reviews", [])
        if not reviews:
            return count, False
        for rv in reviews:
            if rv.get("timestamp_created", 0) >= cutoff:
                count += 1
            else:
                return count, False
        new_cursor = d.get("cursor")
        if not new_cursor or new_cursor == cursor:
            return count, False
        cursor = new_cursor
    return count, True  # sayfa sınırına ulaşıldı


def current_players(f, appid):
    try:
        d = f.get_json(CURRENT_PLAYERS, {"appid": appid}, delay=0.3)
        return d.get("response", {}).get("player_count")
    except RuntimeError as e:
        f.record_error("steam_web_api", e, appid=appid)
        return None


def youtube_median_views(f, key, name):
    """Oyun adıyla aranan ilk 10 videonun medyan izlenmesi (arama = 100 kota birimi)."""
    if not key:
        return None
    try:
        s = f.get_json(YT_SEARCH, {
            "key": key, "q": f'"{name}" game', "part": "id",
            "type": "video", "maxResults": 10, "order": "relevance",
        }, delay=0.2)
        ids = [it["id"]["videoId"] for it in s.get("items", []) if "videoId" in it.get("id", {})]
        if not ids:
            return 0
        v = f.get_json(YT_VIDEOS, {"key": key, "id": ",".join(ids), "part": "statistics"}, delay=0.2)
        views = [int(it["statistics"].get("viewCount", 0)) for it in v.get("items", [])]
        return statistics.median(views) if views else 0
    except (RuntimeError, KeyError) as e:
        f.record_error("youtube", e, oyun=name)
        return None


def parse_release(s):
    for fmt in ("%b %d, %Y", "%d %b, %Y", "%B %d, %Y", "%d %B, %Y", "%b %Y", "%Y"):
        try:
            return dt.datetime.strptime(s.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


# ------------------------------------------------------------- hesaplama ---
def wilson_lower(pos, n, z=1.96):
    if not n:
        return None
    p = pos / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    adj = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - adj) / denom


def percentiles(values):
    """Ölçülebilen değerleri gruptaki yüzdelik dilime (0-1) çevirir."""
    known = sorted(v for v in values if v is not None)
    n = len(known)
    out = []
    for v in values:
        if v is None or n == 0:
            out.append(None)
            continue
        less = sum(1 for k in known if k < v)
        equal = sum(1 for k in known if k == v)
        out.append((less + 0.5 * equal) / n)
    return out


def compute_metrics(g):
    age = g["yas_ay"]
    t = age ** TIME_EXP
    total, pos = g["toplam_yorum"], g["pozitif_yorum"]
    monthly_avg = total / age if age else 0
    return {
        "yorum_hizi": total / t,
        "pozitif_oran": wilson_lower(pos, total),
        "aktif_oyuncu": g["zirve_ccu_dun"] if g["zirve_ccu_dun"] is not None else g["anlik_oyuncu"],
        "gelir_hizi": total * REVIEW_TO_SALES * g["fiyat_usd"] / t,
        "guncel_ivme": (g["son30_yorum"] / monthly_avg) if monthly_avg else None,
        "sosyal": g["yt_medyan_izlenme"],
    }


def score_all(games, weights=None):
    weights = weights or WEIGHTS
    for g in games:
        g["metrikler"] = compute_metrics(g)
    pct = {m: percentiles([g["metrikler"][m] for g in games]) for m in weights}
    for i, g in enumerate(games):
        got, wsum, parts = 0.0, 0.0, {}
        for m, w in weights.items():
            if w <= 0:
                continue
            p = pct[m][i]
            if p is None:
                continue
            parts[m] = round(p * w, 2)
            got += p * w
            wsum += w
        g["puan_detay"] = parts
        g["skor"] = round(got / wsum * 100, 1) if wsum else 0.0
    games.sort(key=lambda g: g["skor"], reverse=True)
    return games


# ------------------------------------------------------------------- akış ---
def collect(f, appids, spy_rows, args, yt_key, on_progress=None, top=None, stop_after_old=40,
            rank_rows=None):
    """
    1) Ön eleme: sadece mağaza detayıyla tarih/fiyat/tür kontrolü (ucuz, önbellekli).
       Liste yeniden eskiye sıralıysa, art arda `stop_after_old` oyun pencereden
       eski çıkınca tarama durur.
    2) Sıralama: pencereye giren oyunlar SteamSpy yorum sayısına göre sıralanır,
       en başarılı `top` tanesi seçilir.
    3) Ağır veri: yalnızca seçilenler için yorum, oyuncu ve sosyal veri çekilir.
    """
    today = dt.date.today()
    games, skipped, passed = [], {}, []
    oldest_seen, old_streak, scanned = None, 0, 0

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    def progress(phase, i, n, extra=""):
        if on_progress:
            on_progress(phase, i, n, extra)
        else:
            print(f"  {phase} [{i}/{n}] {extra}      ", end="\r", flush=True)

    # --- 1. aşama: ön eleme
    for i, appid in enumerate(appids, 1):
        progress("Ön eleme", i, len(appids), f"{len(passed)} oyun pencerede")
        scanned = i
        try:
            det = store_details(f, appid)
        except RuntimeError as e:
            skip("ağ hatası")
            f.record_error("oyun_verisi", e, appid=appid)
            continue
        if not det or det.get("type") != "game":
            skip("oyun değil / bulunamadı"); continue
        if det.get("release_date", {}).get("coming_soon"):
            skip("henüz çıkmadı"); continue
        rel = parse_release(det.get("release_date", {}).get("date", ""))
        if not rel:
            skip("çıkış tarihi okunamadı"); continue
        oldest_seen = min(oldest_seen, rel) if oldest_seen else rel
        age_days = (today - rel).days
        if age_days > args.max_age_months * 30.44:
            skip(f"{args.max_age_months} aydan eski")
            old_streak += 1
            if stop_after_old and old_streak >= stop_after_old:
                break
            continue
        old_streak = 0
        if age_days < args.min_age_days:
            skip(f"{args.min_age_days} günden yeni"); continue
        is_free = det.get("is_free", False)
        po = det.get("price_overview") or {}
        price = (po.get("initial") or po.get("final") or 0) / 100
        if is_free and not args.include_free:
            skip("ücretsiz"); continue
        if price > args.max_price:
            skip(f"{args.max_price}$ üstü"); continue
        pubs = [x.lower() for x in det.get("publishers", [])]
        if any(x.lower() in pubs for x in args.exclude_publisher):
            skip("hariç tutulan yayıncı"); continue
        passed.append((appid, det, rel, age_days, price))

    # --- 2. aşama: pencere içindeki en başarılılar
    rank_rows = rank_rows if rank_rows is not None else spy_rows

    def spy_reviews(appid):
        r = rank_rows.get(str(appid)) or {}
        return r.get("positive", 0) + r.get("negative", 0)
    if rank_rows:
        passed.sort(key=lambda x: spy_reviews(x[0]), reverse=True)
    if top and len(passed) > top:
        skipped["sıralama dışı (top sınırı)"] = len(passed) - top
        passed = passed[:top]

    # --- 3. aşama: ağır veri
    for j, (appid, det, rel, age_days, price) in enumerate(passed, 1):
        progress("Veri toplama", j, len(passed), det.get("name", "")[:30])
        try:
            rs = review_summary(f, appid)
            total = rs.get("total_reviews", 0)
            if total < args.min_reviews:
                skip(f"{args.min_reviews} yorumdan az"); continue
            spy = spy_rows.get(str(appid)) or steamspy_app(f, appid)
            recent, capped = recent_review_count(f, appid)
            games.append({
                "appid": appid,
                "ad": det.get("name", ""),
                "gelistirici": ", ".join(det.get("developers", [])),
                "yayinci": ", ".join(det.get("publishers", [])),
                "cikis": rel.isoformat(),
                "yas_ay": round(max(age_days / 30.44, 1.0), 2),
                "fiyat_usd": price,
                "toplam_yorum": total,
                "pozitif_yorum": rs.get("total_positive", 0),
                "pozitif_yuzde": round(100 * rs.get("total_positive", 0) / total, 1),
                "son30_yorum": recent,
                "son30_alt_sinir": capped,
                "zirve_ccu_dun": spy.get("ccu") if spy else None,
                "anlik_oyuncu": current_players(f, appid),
                "yt_medyan_izlenme": youtube_median_views(f, yt_key, det.get("name", "")),
                "tahmini_satis": total * REVIEW_TO_SALES,
            })
        except RuntimeError as e:
            skip("ağ hatası")
            f.record_error("oyun_verisi", e, appid=appid)
    if not on_progress:
        print()
    coverage = {
        "aday": len(appids), "taranan": scanned,
        "taranan_en_eski_cikis": oldest_seen.isoformat() if oldest_seen else None,
        "pencere_tamamen_tarandi": bool(
            oldest_seen and (today - oldest_seen).days > args.max_age_months * 30.44),
    }
    return games, skipped, coverage


def steam_tag_id(f, tag):
    """Etiket adını Steam'in sayısal etiket kimliğine çevirir."""
    rows = f.get_json(TAG_LIST, delay=0.5) or []
    for r in rows:
        if r.get("name", "").strip().lower() == tag.strip().lower():
            return r.get("tagid")
    return None


def _parse_search_html(html_text):
    """Mağaza arama sonucundaki her oyun için appid, çıkış, yorum sayısı ve fiyat."""
    out = []
    for block in html_text.split('data-ds-appid="')[1:]:
        raw_id = block.split('"', 1)[0]
        if not raw_id.isdigit():          # paketler birden çok appid taşır, atla
            continue
        rel = re.search(r'search_released[^>]*>\s*([^<]*?)\s*<', block)
        rv = re.search(r'of the ([\d,\.]+) user reviews', block)
        price = re.search(r'data-price-final="(\d+)"', block)
        out.append({
            "appid": int(raw_id),
            "cikis": parse_release(rel.group(1)) if rel and rel.group(1) else None,
            "yorum": int(re.sub(r"[^\d]", "", rv.group(1))) if rv else 0,
            "fiyat_kurus": int(price.group(1)) if price else None,
        })
    return out


def steam_search_recent(f, tagid, max_age_months, min_reviews, max_price,
                        max_pages=120, page_size=50):
    """
    Steam mağaza aramasından etiketteki oyunları yeniden eskiye çeker; pencerenin
    dışına çıkınca durur. Yorum ve fiyatı arama sonucundan okuyarak ön eleme yapar.
    """
    today = dt.date.today()
    cutoff = today - dt.timedelta(days=max_age_months * 30.44)
    rows, oldest, seen, start, old_run = {}, None, 0, 0, 0
    done = False
    for _ in range(max_pages):
        d = f.get_json(STORE_SEARCH, {
            "infinite": 1, "tags": tagid, "category1": 998, "sort_by": "Released_DESC",
            "start": start, "count": page_size, "cc": "us", "l": "english",
        }, delay=1.5) or {}
        items = _parse_search_html(d.get("results_html", ""))
        if not items:
            done = True
            break
        for it in items:
            seen += 1
            if it["cikis"]:
                oldest = min(oldest, it["cikis"]) if oldest else it["cikis"]
                if it["cikis"] < cutoff:
                    old_run += 1
                    continue
                old_run = 0
            if it["yorum"] < min_reviews:
                continue
            if it["fiyat_kurus"] is not None and it["fiyat_kurus"] / 100 > max_price:
                continue
            rows[str(it["appid"])] = {"appid": it["appid"], "positive": it["yorum"], "negative": 0}
        start += len(items)
        if old_run >= 15:                     # sıralı liste: pencere bitti
            done = True
            break
        total = d.get("total_count")
        if total is not None and start >= int(total):
            done = True
            break
    info = {"kaynak": "Steam mağaza araması", "arama_taranan": seen,
            "taranan_en_eski_cikis": oldest.isoformat() if oldest else None,
            "pencere_tamamen_tarandi": done}
    return rows, info


def tag_candidates(f, tag, max_price, min_reviews, top, max_age_months=36, scan_factor=5):
    """
    Adayları döndürür: (steamspy_satırları, appid_listesi, sıralama_satırları, bilgi).
    Önce Steam mağaza aramasını dener (güncel, tarihe göre sıralı); olmazsa SteamSpy.
    """
    try:
        tagid = steam_tag_id(f, tag)
        if tagid is None:
            f.record_error("etiket", f"'{tag}' Steam etiket listesinde bulunamadı, SteamSpy'a "
                           f"geçildi. Yazımı Steam'deki gibi mi?")
        else:
            rows, info = steam_search_recent(f, tagid, max_age_months, min_reviews, max_price)
            info["etiket_id"] = tagid
            appids = [r["appid"] for r in rows.values()]
            return {}, appids, rows, info
    except RuntimeError as e:
        f.record_error("steam_arama", f"Mağaza araması başarısız, SteamSpy'a geçildi: {e}")

    spy_rows = steamspy_tag(f, tag) or {}
    cands = [r for r in spy_rows.values()
             if int(r.get("initialprice") or 0) / 100 <= max_price
             and (r.get("positive", 0) + r.get("negative", 0)) >= min_reviews]
    cands.sort(key=lambda r: int(r["appid"]), reverse=True)
    info = {"kaynak": "SteamSpy (yedek)", "etiketteki_oyun": len(spy_rows)}
    return spy_rows, [int(r["appid"]) for r in cands[:top * scan_factor]], spy_rows, info


def write_outputs(games, skipped, args, label):
    base = Path(args.out or f"sonuclar_{label}")
    cols = ["sira", "skor", "ad", "appid", "cikis", "yas_ay", "fiyat_usd", "toplam_yorum",
            "pozitif_yuzde", "son30_yorum", "zirve_ccu_dun", "anlik_oyuncu",
            "yt_medyan_izlenme", "tahmini_satis", "gelistirici", "yayinci"] + \
           [f"puan_{m}" for m in WEIGHTS]
    with open(base.with_suffix(".csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for i, g in enumerate(games, 1):
            row = {**g, "sira": i, **{f"puan_{m}": g["puan_detay"].get(m, "") for m in WEIGHTS}}
            w.writerow([row.get(c, "") for c in cols])

    meta = {
        "etiket_veya_liste": label,
        "olusturma": dt.datetime.now().isoformat(timespec="seconds"),
        "ayarlar": {"agirliklar": WEIGHTS, "zaman_ussu": TIME_EXP,
                    "yorum_satis_carpani": REVIEW_TO_SALES, **vars(args)},
        "elenenler": skipped,
        "oyunlar": games,
    }
    with open(base.with_suffix(".json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    return base


def main():
    ap = argparse.ArgumentParser(description="Steam indie tür başarı skoru")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--tag", help='Steam etiketi, ör. "Roguelike Deckbuilder"')
    src.add_argument("--appids", nargs="+", type=int, help="Belirli oyunların Steam ID'leri")
    ap.add_argument("--top", type=int, default=150, help="Etiketten en çok yorumlu N oyunu incele")
    ap.add_argument("--max-price", type=float, default=30, help="Üst fiyat (USD), indie filtresi")
    ap.add_argument("--min-reviews", type=int, default=50)
    ap.add_argument("--min-age-days", type=int, default=30)
    ap.add_argument("--max-age-months", type=int, default=36)
    ap.add_argument("--include-free", action="store_true", help="Ücretsiz oyunları da dahil et")
    ap.add_argument("--exclude-publisher", action="append", default=[],
                    help="Hariç tutulacak yayıncı (birden çok kez verilebilir)")
    ap.add_argument("--out", help="Çıktı dosya adı (uzantısız)")
    ap.add_argument("--cache-dir", default="cache")
    args = ap.parse_args()

    yt_key = os.environ.get("YOUTUBE_API_KEY")
    if not yt_key:
        print("Bilgi: YOUTUBE_API_KEY yok, sosyal puan atlanıp ağırlığı dağıtılacak.")

    f = Fetcher(args.cache_dir, secrets=[yt_key])
    spy_rows = {}
    if args.tag:
        print(f"SteamSpy: '{args.tag}' etiketi alınıyor...")
        spy_rows, appids, rank_rows, info = tag_candidates(
            f, args.tag, args.max_price, args.min_reviews, args.top, args.max_age_months)
        label = args.tag.replace(" ", "_")
        print(f"Kaynak: {info.get('kaynak')}, {len(appids)} aday incelenecek.")
    else:
        appids, label, rank_rows, info = args.appids, "liste", None, {}
        spy_rows = {}

    games, skipped, coverage = collect(f, appids, spy_rows, args, yt_key,
                                       top=args.top if args.tag else None,
                                       stop_after_old=40 if spy_rows else None,
                                       rank_rows=rank_rows)
    coverage.update(info)
    print(f"Kapsam: {coverage}")
    if not games:
        print("Filtrelerden geçen oyun yok. Elenenler:", skipped)
        sys.exit(1)

    score_all(games)
    base = write_outputs(games, skipped, args, label)

    print(f"\n{len(games)} oyun puanlandı. Elenenler: {skipped}")
    print(f"{'#':>3} {'Skor':>5}  {'Oyun':<38} {'Yorum':>7} {'%Poz':>5} {'Ay':>5} {'Fiyat':>6}")
    for i, g in enumerate(games[:15], 1):
        print(f"{i:>3} {g['skor']:>5}  {g['ad'][:38]:<38} {g['toplam_yorum']:>7} "
              f"{g['pozitif_yuzde']:>5} {g['yas_ay']:>5} {g['fiyat_usd']:>6}")
    print(f"\nÇıktılar: {base.with_suffix('.csv')}  ve  {base.with_suffix('.json')}")


if __name__ == "__main__":
    main()
