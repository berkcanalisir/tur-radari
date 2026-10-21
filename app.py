"""
app.py — Başarı skoru için web arayüzü (Streamlit)
Yerelde: streamlit run app.py   |   Bulutta: Streamlit Community Cloud
"""
import datetime as dt
import json
import platform
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import requests
import streamlit as st

import basari_skoru as bs

APP_VERSION = "0.3"
CACHE_DIR = "cache"

st.set_page_config(page_title="Tür Radarı", page_icon="🎮", layout="wide")

METRIC_LABELS = {
    "yorum_hizi": "Yorum hızı",
    "pozitif_oran": "Pozitif oran",
    "aktif_oyuncu": "Aktif oyuncu",
    "gelir_hizi": "Gelir hızı",
    "guncel_ivme": "Güncel ivme",
    "sosyal": "Sosyal medya",
}

# YouTube anahtarı Streamlit "Secrets" bölümünden okunur (opsiyonel)
try:
    YT_KEY = st.secrets.get("YOUTUBE_API_KEY")
except Exception:
    YT_KEY = None



@st.cache_resource
def process_start():
    """Sunucu sürecinin başladığı an (uykudan uyanınca sıfırlanır)."""
    return time.time()


START = process_start()

if "fetcher" not in st.session_state:
    st.session_state["fetcher"] = bs.Fetcher(CACHE_DIR, secrets=[YT_KEY])
F = st.session_state["fetcher"]


def blocking_error(kind, exc, **extra):
    F.record_error(kind, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                   severity="engelleyici", **extra)


def cache_info():
    files = list(Path(CACHE_DIR).glob("*.json"))
    if not files:
        return {"dosya": 0, "mb": 0, "en_eski_saat": None}
    ages = [(time.time() - f.stat().st_mtime) / 3600 for f in files]
    return {"dosya": len(files), "mb": round(sum(f.stat().st_size for f in files) / 1e6, 2),
            "en_eski_saat": round(max(ages), 1)}


def _ping(url, params, ok_fn):
    t0 = time.time()
    try:
        r = requests.get(url, params=params, timeout=8,
                         headers={"User-Agent": "indie-genre-research/0.1"})
        ms = int((time.time() - t0) * 1000)
        if r.status_code == 429:
            return "warn", f"İstek sınırı (429), {ms} ms"
        if r.status_code != 200:
            reason = ""
            try:
                reason = r.json().get("error", {}).get("errors", [{}])[0].get("reason", "")
            except Exception:
                pass
            return "err", f"HTTP {r.status_code} {reason}".strip()
        return ("ok", f"Bağlı, {ms} ms") if ok_fn(r.json()) else ("err", "Beklenmeyen yanıt")
    except requests.Timeout:
        return "err", "Zaman aşımı (8 sn)"
    except Exception as e:
        return "err", f"{type(e).__name__}: {str(e)[:80]}"


@st.cache_data(ttl=300, show_spinner=False)
def check_systems(yt_key, _nonce):
    """Her bağlantıyı küçük bir test isteğiyle dener (5 dk önbellekli)."""
    test_app = 440
    rows = [
        ("Steam mağaza", *_ping(bs.STORE_DETAILS, {"appids": test_app, "cc": "us"},
                                lambda d: d.get(str(test_app), {}).get("success"))),
        ("Steam yorumlar", *_ping(bs.STORE_REVIEWS.format(appid=test_app),
                                  {"json": 1, "num_per_page": 0},
                                  lambda d: "query_summary" in d)),
        ("Steam Web API", *_ping(bs.CURRENT_PLAYERS, {"appid": test_app},
                                 lambda d: "player_count" in d.get("response", {}))),
        ("SteamSpy", *_ping(bs.STEAMSPY, {"request": "appdetails", "appid": test_app},
                            lambda d: "name" in d)),
    ]
    if yt_key:
        rows.append(("YouTube", *_ping(bs.YT_VIDEOS, {"key": yt_key, "id": "jNQXAC9IVRw",
                                                      "part": "id"}, lambda d: "items" in d)))
    else:
        rows.append(("YouTube", "off", "Bağlı değil (anahtar yok)"))
    return rows, dt.datetime.now().strftime("%H:%M:%S")


def local_rows():
    up_min = (time.time() - START) / 60
    if up_min < 10:
        srv = ("warn", f"Yeni başladı ({up_min:.0f} dk), uykudan uyanmış olabilir")
    else:
        srv = ("ok", f"Uyanık, {up_min / 60:.1f} saattir çalışıyor")
    ci = cache_info()
    if ci["dosya"] == 0:
        cache = ("warn", "Boş, silinmiş olabilir (ilk analiz uzun sürer)")
    else:
        cache = ("ok", f"{ci['dosya']} dosya, {ci['mb']} MB, en eskisi {ci['en_eski_saat']} sa")
    return [("Sunucu", *srv), ("Önbellek", *cache)]


def build_report(sys_rows, checked_at):
    lines = ["=== TÜR RADARI TANI RAPORU ===",
             f"uygulama {APP_VERSION} | python {sys.version.split()[0]} | "
             f"streamlit {st.__version__} | {platform.platform()}",
             f"rapor zamanı: {dt.datetime.now().isoformat(timespec='seconds')}",
             f"sunucu çalışma süresi: {(time.time() - START) / 60:.1f} dk",
             "", "--- SON ANALİZ AYARLARI ---",
             json.dumps(st.session_state.get("settings", "henüz analiz yok"), ensure_ascii=False),
             "", f"--- SİSTEM DURUMU (kontrol {checked_at}) ---"]
    lines += [f"{name}: {state.upper()} | {msg}" for name, state, msg in sys_rows]
    lines += ["", "--- ÖNBELLEK ---", json.dumps(cache_info()),
              "", "--- İSTEK İSTATİSTİĞİ ---", json.dumps(F.stats, ensure_ascii=False),
              "", "--- ELENENLER ---",
              json.dumps(st.session_state.get("skipped", {}), ensure_ascii=False),
              "", f"--- HATALAR ({len(F.errors)}) ---"]
    for i, e in enumerate(F.errors[-40:], 1):
        extra = {k: v for k, v in e.items() if k not in ("zaman", "onem", "tur", "mesaj")}
        lines.append(f"[{i}] {e['zaman']} | {e['onem']} | {e['tur']} | {extra}")
        lines.append("    " + e["mesaj"].replace("\n", "\n    "))
    lines += ["", "--- SON 30 HTTP İSTEĞİ ---"]
    lines += [f"{x['zaman']} {x['durum']} {x['ms']}ms {x['url']}" for x in list(F.log)[-30:]]
    lines.append("=== RAPOR SONU ===")
    return F.mask("\n".join(lines))


def render_footer():
    st.divider()
    if "nonce" not in st.session_state:
        st.session_state["nonce"] = 0
    sys_rows, checked_at = check_systems(YT_KEY, st.session_state["nonce"])
    sys_rows = sys_rows + local_rows()
    icons = {"ok": "🟢", "warn": "🟡", "err": "🔴", "off": "⚪"}

    left, right = st.columns([3, 2])
    with left:
        blockers = [e for e in F.errors if e["onem"] == "engelleyici"]
        st.subheader(f"Hata listesi ({len(F.errors)})")
        if not F.errors:
            st.caption("Kayıtlı hata yok.")
        for e in reversed(F.errors[-15:]):
            icon = "⛔" if e["onem"] == "engelleyici" else "⚠️"
            target = e.get("appid") or e.get("oyun") or ""
            with st.container(border=True):
                st.markdown(f"{icon} **{e['tur']}** {target} · {e['zaman']} · {e['onem']}")
                st.caption(e["mesaj"].split("\n")[0][:220])
        if len(F.errors) > 15:
            st.caption(f"…ve {len(F.errors) - 15} hata daha (tamamı tanı raporunda).")
        c1, c2 = st.columns(2)
        if c1.button("Hata listesini temizle", width="stretch", disabled=not F.errors):
            F.errors.clear()
            st.rerun()
        report = build_report(sys_rows, checked_at)
        c2.download_button("Tanı raporunu indir (.txt)", report,
                           f"tani_raporu_{dt.datetime.now():%Y%m%d_%H%M}.txt", width="stretch")
        with st.expander("Tanı raporu: kopyalayıp Claude'a gönderin", expanded=bool(blockers)):
            st.code(report, language=None)

    with right:
        st.subheader("Sistem durumu")
        with st.container(border=True):
            for name, state, msg in sys_rows:
                a, b = st.columns([2, 3])
                a.markdown(f"{icons[state]} **{name}**")
                b.caption(msg)
        st.caption(f"Son kontrol: {checked_at} (5 dk'da bir yenilenir)")
        if st.button("Şimdi yeniden kontrol et", width="stretch"):
            st.session_state["nonce"] += 1
            st.rerun()


# ------------------------------------------------------------- kenar çubuğu
with st.sidebar:
    st.header("Analiz ayarları")
    tag = st.text_input("Steam etiketi", "Roguelike Deckbuilder",
                        help="Steam'de göründüğü gibi, İngilizce")
    top = st.slider("İncelenecek oyun sayısı", 20, 300, 100, step=10,
                    help="Etiketteki en çok yorumlu N oyun. Fazlası ilk çalıştırmayı uzatır.")
    max_price = st.slider("Üst fiyat (USD)", 5, 60, 30)
    min_reviews = st.number_input("En az yorum", 10, 5000, 50, step=10)
    age = st.slider("Oyun yaşı (ay)", 1, 60, (1, 36))
    include_free = st.checkbox("Ücretsiz oyunları dahil et")
    run = st.button("Analizi başlat", type="primary", width="stretch")

    st.divider()
    st.subheader("Puan ağırlıkları")
    st.caption("Değiştirince sonuçlar veri çekmeden yeniden puanlanır.")
    weights = {}
    for m, default in bs.WEIGHTS.items():
        disabled = m == "sosyal" and not YT_KEY
        weights[m] = st.slider(METRIC_LABELS[m], 0, 50, 0 if disabled else default,
                               disabled=disabled, key=f"w_{m}")
    if not YT_KEY:
        st.caption("Sosyal medya için Secrets'a YOUTUBE_API_KEY ekleyin.")

# ------------------------------------------------------------------ veri çek
if run:
    args = SimpleNamespace(max_price=max_price, min_reviews=min_reviews,
                           min_age_days=max(age[0] * 30, 30), max_age_months=age[1],
                           include_free=include_free, exclude_publisher=[])
    st.session_state["settings"] = {"etiket": tag, **vars(args), "top": top}
    f = F
    try:
        with st.status(f"'{tag}' analiz ediliyor...", expanded=True) as status:
            st.write("SteamSpy'dan etiket listesi alınıyor")
            spy_rows, appids = bs.tag_candidates(f, tag, max_price, min_reviews, top)
            if not appids:
                status.update(label="Bu etikette uygun oyun bulunamadı", state="error")
                F.record_error("etiket", f"'{tag}' etiketi boş döndü veya filtrelerden oyun geçmedi "
                               f"(SteamSpy satır sayısı: {len(spy_rows)})", severity="engelleyici")
                st.session_state.pop("games", None)
            else:
                st.write(f"Etikette {len(spy_rows)} oyun var, {len(appids)} tanesi inceleniyor")
                bar = st.progress(0.0)
                games, skipped = bs.collect(
                    f, appids, spy_rows, args, YT_KEY,
                    on_progress=lambda i, n, a: bar.progress(i / n, text=f"{i}/{n} oyun"))
                if not games and skipped.get("ağ hatası"):
                    F.record_error("analiz", f"İncelenen {len(appids)} oyunun hiçbirinden veri "
                                   f"alınamadı. Elenenler: {skipped}", severity="engelleyici")
                    status.update(label="Veri alınamadı", state="error")
                else:
                    status.update(label=f"{len(games)} oyun puanlandı", state="complete",
                                  expanded=False)
                st.session_state.update(games=games, skipped=skipped, tag=tag)
    except Exception as e:
        blocking_error("analiz", e, etiket=tag)
        st.session_state.pop("games", None)

# ------------------------------------------------------------------ sonuçlar
st.title("🎮 Tür Radarı")

if any(e["onem"] == "engelleyici" for e in F.errors):
    st.error("Analizi durduran bir hata oluştu. Sayfanın altındaki tanı raporunu kopyalayıp "
             "Claude'a gönderin.", icon="⛔")

if "games" not in st.session_state:
    st.info("Soldan bir Steam etiketi seçip **Analizi başlat**'a basın. "
            "İlk çalıştırma 100 oyun için 10–15 dakika sürebilir; sonrakiler önbellekten hızlı gelir.")
    render_footer()
    st.stop()

games = st.session_state["games"]
if not games:
    st.warning(f"Filtrelerden geçen oyun yok. Elenenler: {st.session_state['skipped']}")
    render_footer()
    st.stop()
if sum(weights.values()) == 0:
    st.warning("En az bir ağırlık sıfırdan büyük olmalı.")
    render_footer()
    st.stop()

bs.score_all(games, weights)
df = pd.DataFrame([{
    "Skor": g["skor"], "Oyun": g["ad"], "Çıkış": g["cikis"], "Ay": g["yas_ay"],
    "Fiyat $": g["fiyat_usd"], "Yorum": g["toplam_yorum"], "% Pozitif": g["pozitif_yuzde"],
    "Son 30 gün": g["son30_yorum"], "Zirve oyuncu (dün)": g["zirve_ccu_dun"],
    "Tahmini satış": g["tahmini_satis"], "Geliştirici": g["gelistirici"],
    "Steam": f"https://store.steampowered.com/app/{g['appid']}",
} for g in games])

st.caption(f"Etiket: **{st.session_state['tag']}**  ·  Elenenler: "
           + ", ".join(f"{k}: {v}" for k, v in st.session_state["skipped"].items()))

c1, c2, c3, c4 = st.columns(4)
c1.metric("Puanlanan oyun", len(df))
c2.metric("Medyan pozitif", f"%{df['% Pozitif'].median():.0f}")
c3.metric("Medyan fiyat", f"${df['Fiyat $'].median():.2f}")
c4.metric("Medyan tahmini satış", f"{df['Tahmini satış'].median():,.0f}".replace(",", "."))

tab1, tab2, tab3 = st.tabs(["Sıralama", "Dağılım", "Oyun detayı"])

with tab1:
    st.dataframe(df, hide_index=True, width="stretch", height=560, column_config={
        "Skor": st.column_config.ProgressColumn("Skor", min_value=0, max_value=100, format="%.1f"),
        "Steam": st.column_config.LinkColumn("Steam", display_text="Aç"),
        "Fiyat $": st.column_config.NumberColumn(format="$%.2f"),
    })

with tab2:
    st.write("Her nokta bir oyun. Sağ üste yakın olanlar hem yeni hem çok yorum almış oyunlar.")
    st.scatter_chart(df, x="Ay", y="Yorum", size="Skor", color="Skor", height=480)
    st.write("Fiyat bandına göre ortalama skor")
    bands = pd.cut(df["Fiyat $"], [0, 5, 10, 15, 20, 30, 60],
                   labels=["0–5", "5–10", "10–15", "15–20", "20–30", "30+"])
    st.bar_chart(df.groupby(bands, observed=True)["Skor"].mean(), height=300)

with tab3:
    choice = st.selectbox("Oyun seç", df["Oyun"])
    g = next(x for x in games if x["ad"] == choice)
    left, right = st.columns([1, 2])
    left.metric("Skor", g["skor"])
    left.write(f"**Çıkış:** {g['cikis']} ({g['yas_ay']} ay önce)")
    left.write(f"**Fiyat:** ${g['fiyat_usd']}  ·  **Yorum:** {g['toplam_yorum']} (%{g['pozitif_yuzde']})")
    left.write(f"**Geliştirici:** {g['gelistirici']}")
    parts = pd.Series({METRIC_LABELS[k]: v for k, v in g["puan_detay"].items()})
    right.write("Skorun metriklere göre dağılımı (ağırlıklı puan)")
    right.bar_chart(parts, horizontal=True, height=280)

st.divider()
d1, d2 = st.columns(2)
d1.download_button("CSV indir", df.to_csv(index=False).encode("utf-8-sig"),
                   f"sonuclar_{st.session_state['tag']}.csv", "text/csv", width="stretch")
d2.download_button("Analist ajan için JSON indir",
                   json.dumps({"etiket": st.session_state["tag"], "agirliklar": weights,
                               "elenenler": st.session_state["skipped"], "oyunlar": games},
                              ensure_ascii=False, indent=2),
                   f"sonuclar_{st.session_state['tag']}.json", "application/json",
                   width="stretch")

render_footer()
