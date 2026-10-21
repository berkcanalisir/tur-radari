"""
app.py — Tür Radarı: Steam tür analizi için web arayüzü (Streamlit)
Yerelde: streamlit run app.py   |   Bulutta: Streamlit Community Cloud
"""
import datetime as dt
import html
import json
import platform
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import altair as alt
import pandas as pd
import requests
import streamlit as st

import basari_skoru as bs

APP_VERSION = "0.5"
CACHE_DIR = "cache"

st.set_page_config(page_title="Tür Radarı", page_icon="📡", layout="wide",
                   initial_sidebar_state="collapsed")

# ---------------------------------------------------------------- tasarım
INK, PANEL, LINE = "#141A2E", "#1C2340", "#2C3558"
TEXT, MUTED = "#E9ECF7", "#8D94B8"
HEAT = ["#F2685A", "#F2B84B", "#3FD3A0"]  # düşük, orta, yüksek skor

METRIC_LABELS = {
    "yorum_hizi": "Yorum hızı", "pozitif_oran": "Pozitif oran",
    "aktif_oyuncu": "Aktif oyuncu", "gelir_hizi": "Gelir hızı",
    "guncel_ivme": "Güncel ivme", "sosyal": "Sosyal medya",
}
EXAMPLE_TAGS = ["Roguelike Deckbuilder", "Cozy", "Survivors-like", "Automation", "Metroidvania"]


def heat(score):
    """0-100 skoru mercan → kehribar → nane skalasında renge çevirir."""
    t = max(0.0, min(1.0, (score or 0) / 100))
    a, b, k = (HEAT[0], HEAT[1], t / 0.5) if t < 0.5 else (HEAT[1], HEAT[2], (t - 0.5) / 0.5)
    ca, cb = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * k):02X}" for x, y in zip(ca, cb))


def tr_num(n):
    return f"{n:,.0f}".replace(",", ".")


st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');
html, body, .stApp, .stMarkdown p, input, textarea, button p {{
  font-family: 'IBM Plex Sans', system-ui, sans-serif; }}
h1, h2, h3, .rd-display {{ font-family: 'Bricolage Grotesque', 'IBM Plex Sans', sans-serif !important;
  letter-spacing: -0.01em; }}
.block-container {{ padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1680px; }}
#MainMenu, footer, [data-testid="stSidebarCollapsedControl"] {{ visibility: hidden; }}
.rd-brand {{ display:flex; align-items:baseline; gap:14px; margin-bottom:.2rem; }}
.rd-title {{ font-family:'Bricolage Grotesque',sans-serif; font-size:2.3rem; font-weight:700;
  color:{TEXT}; line-height:1.1; }}
.rd-brand span {{ color:{MUTED}; font-size:.95rem; }}
.stMarkdown p.rd-hint, p.rd-hint {{ font-size:.85rem !important; color:{MUTED}; }}
.rd-kpis {{ display:grid; grid-template-columns: repeat(5, 1fr); border:1px solid {LINE};
  border-radius:14px; background:{PANEL}; margin:.4rem 0 1.1rem; }}
.rd-kpi {{ padding:14px 20px; border-left:1px solid {LINE}; }}
.rd-kpi:first-child {{ border-left:none; }}
.rd-kpi b {{ display:block; font-family:'Bricolage Grotesque',sans-serif; font-size:1.55rem;
  font-weight:700; color:{TEXT}; font-variant-numeric: tabular-nums; }}
.rd-kpi small {{ color:{MUTED}; font-size:.82rem; }}
.rd-section {{ font-family:'Bricolage Grotesque',sans-serif; font-size:1.15rem; font-weight:700;
  color:{TEXT}; margin:.2rem 0 .5rem; }}
.rd-hint {{ color:{MUTED}; font-size:.85rem; margin:-.3rem 0 .6rem; }}
.rd-game {{ background:{PANEL}; border:1px solid {LINE}; border-radius:14px; padding:22px 24px; }}
.rd-score {{ font-family:'Bricolage Grotesque',sans-serif; font-weight:700; font-size:5.2rem;
  line-height:.9; font-variant-numeric: tabular-nums; }}
.rd-score small {{ font-size:1.1rem; color:{MUTED}; font-weight:500; margin-left:4px; }}
.rd-name {{ font-family:'Bricolage Grotesque',sans-serif; font-size:1.45rem; font-weight:700;
  color:{TEXT}; margin:12px 0 2px; line-height:1.2; }}
.rd-meta {{ color:{MUTED}; font-size:.9rem; margin-bottom:16px; }}
.rd-meta a {{ color:{HEAT[2]}; text-decoration:none; }}
.rd-facts {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:18px; }}
.rd-facts div {{ border-top:1px solid {LINE}; padding-top:8px; }}
.rd-facts b {{ display:block; color:{TEXT}; font-size:1.05rem; font-variant-numeric: tabular-nums; }}
.rd-facts small {{ color:{MUTED}; font-size:.78rem; }}
.rd-bar {{ display:grid; grid-template-columns: 110px 1fr 64px; align-items:center; gap:12px;
  margin:7px 0; font-size:.88rem; color:{TEXT}; }}
.rd-bar .track {{ height:8px; background:{INK}; border-radius:4px; overflow:hidden; }}
.rd-bar .fill {{ height:100%; border-radius:4px; }}
.rd-bar em {{ color:{MUTED}; font-style:normal; text-align:right; font-variant-numeric: tabular-nums; }}
.rd-status {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; }}
.rd-chip {{ background:{PANEL}; border:1px solid {LINE}; border-radius:10px; padding:10px 12px; }}
.rd-chip b {{ color:{TEXT}; font-size:.9rem; font-weight:600; }}
.rd-chip p {{ color:{MUTED}; font-size:.8rem; margin:2px 0 0 16px; line-height:1.35; }}
.rd-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:8px; }}
.rd-err {{ border-left:3px solid {LINE}; padding:6px 0 6px 12px; margin-bottom:8px; }}
.rd-err b {{ color:{TEXT}; font-size:.88rem; }}
.rd-err span {{ color:{MUTED}; font-size:.8rem; }}
.rd-err p {{ color:{MUTED}; font-size:.8rem; margin:2px 0 0; word-break:break-word; }}
.rd-empty {{ border:1px dashed {LINE}; border-radius:14px; padding:48px 40px; margin-top:.6rem; }}
.rd-empty h2 {{ font-size:1.9rem; margin:0 0 .4rem; color:{TEXT}; }}
.rd-empty p {{ color:{MUTED}; max-width:68ch; margin:0; }}
@media (max-width: 900px) {{ .rd-kpis {{ grid-template-columns: repeat(2,1fr); }}
  .rd-kpi {{ border-left:none; border-top:1px solid {LINE}; }} .rd-status {{ grid-template-columns:1fr; }} }}
</style>
""", unsafe_allow_html=True)


def chart_theme(chart):
    return (chart.configure(background="transparent",
                             font="'IBM Plex Sans', system-ui, sans-serif")
            .configure_view(strokeWidth=0)
            .configure_axis(labelColor=MUTED, titleColor=MUTED, gridColor=LINE,
                            domainColor=LINE, tickColor=LINE, labelFontSize=11, titleFontSize=11,
                            titleFontWeight=500)
            .configure_legend(labelColor=MUTED, titleColor=MUTED))


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
              "", "--- TARAMA KAPSAMI ---",
             json.dumps(st.session_state.get("coverage", {}), ensure_ascii=False),
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


STATE_COLOR = {"ok": HEAT[2], "warn": HEAT[1], "err": HEAT[0], "off": MUTED}


def render_footer():
    st.markdown("<div style='height:1.2rem'></div>", unsafe_allow_html=True)
    if "nonce" not in st.session_state:
        st.session_state["nonce"] = 0
    sys_rows, checked_at = check_systems(YT_KEY, st.session_state["nonce"])
    sys_rows = sys_rows + local_rows()
    report = build_report(sys_rows, checked_at)
    blockers = [e for e in F.errors if e["onem"] == "engelleyici"]

    left, right = st.columns([7, 5], gap="large")
    with left:
        st.markdown(f"<div class='rd-section'>Hatalar ({len(F.errors)})</div>", unsafe_allow_html=True)
        box = st.container(height=250, border=True)
        if not F.errors:
            box.markdown(f"<p class='rd-hint' style='margin:4px 0'>Kayıtlı hata yok.</p>",
                         unsafe_allow_html=True)
        for e in reversed(F.errors[-30:]):
            color = HEAT[0] if e["onem"] == "engelleyici" else HEAT[1]
            target = html.escape(str(e.get("appid") or e.get("oyun") or ""))
            box.markdown(
                f"<div class='rd-err' style='border-left-color:{color}'>"
                f"<b>{html.escape(e['tur'])} {target}</b> "
                f"<span>{e['zaman']}, {e['onem']}</span>"
                f"<p>{html.escape(e['mesaj'].splitlines()[0][:240])}</p></div>",
                unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        if c1.button("Hataları temizle", width="stretch", disabled=not F.errors):
            F.errors.clear()
            st.rerun()
        c2.download_button("Raporu indir", report,
                           f"tani_raporu_{dt.datetime.now():%Y%m%d_%H%M}.txt", width="stretch")
        with c3.popover("Raporu kopyala", width="stretch"):
            st.caption("Sağ üstteki simgeyle kopyalayıp Claude'a gönderin.")
            st.code(report, language=None)
        if blockers:
            with st.expander("Tanı raporu", expanded=True):
                st.code(report, language=None)

    with right:
        st.markdown("<div class='rd-section'>Sistem durumu</div>", unsafe_allow_html=True)
        chips = "".join(
            f"<div class='rd-chip'><span class='rd-dot' style='background:{STATE_COLOR[s]}'></span>"
            f"<b>{html.escape(n)}</b><p>{html.escape(m)}</p></div>" for n, s, m in sys_rows)
        st.markdown(f"<div class='rd-status'>{chips}</div>", unsafe_allow_html=True)
        a, b = st.columns([3, 2], vertical_alignment="center")
        a.markdown(f"<p class='rd-hint' style='margin:8px 0 0'>Son kontrol {checked_at}, "
                   f"5 dakikada bir yenilenir.</p>", unsafe_allow_html=True)
        if b.button("Yeniden kontrol et", width="stretch"):
            st.session_state["nonce"] += 1
            st.rerun()


def pick_tag(t):
    st.session_state["tag_in"] = t
    st.session_state["autorun"] = True


# ------------------------------------------------------------ üst çubuk
last = st.session_state.get("tag")
st.markdown(
    "<div class='rd-brand'><div class='rd-title'>Tür Radarı</div>"
    f"<span>{'Son analiz: ' + html.escape(last) if last else 'Steam indie tür analizi'}</span></div>",
    unsafe_allow_html=True)

with st.container(border=True):
    c = st.columns([2.4, 1.5, 1.3, 1.5, 0.9, 1.0, 1.1], vertical_alignment="bottom", gap="medium")
    if "tag_in" not in st.session_state:
        st.session_state["tag_in"] = "Roguelike Deckbuilder"
    tag = c[0].text_input("Steam etiketi", key="tag_in", help="Steam'de göründüğü gibi, İngilizce")
    top = c[1].slider("Oyun sayısı", 20, 300, 100, step=10,
                      help="Pencereye giren oyunlardan en başarılı N tanesi")
    max_price = c[2].slider("Üst fiyat $", 5, 60, 30)
    age = c[3].slider("Yaş (ay)", 1, 60, (1, 36))
    min_reviews = c[4].number_input("En az yorum", 10, 5000, 50, step=10)
    with c[5].popover("Ağırlıklar", width="stretch"):
        st.caption("Değiştirince sonuçlar veri çekmeden yeniden puanlanır.")
        weights = {}
        for m, default in bs.WEIGHTS.items():
            off = m == "sosyal" and not YT_KEY
            weights[m] = st.slider(METRIC_LABELS[m], 0, 50, 0 if off else default,
                                   disabled=off, key=f"w_{m}")
        include_free = st.checkbox("Ücretsiz oyunları dahil et")
        if not YT_KEY:
            st.caption("Sosyal medya için Secrets'a YOUTUBE_API_KEY ekleyin.")
    run = c[6].button("Analiz et", type="primary", width="stretch")
    run = run or st.session_state.pop("autorun", False)

# ------------------------------------------------------------------ veri çek
if run:
    args = SimpleNamespace(max_price=max_price, min_reviews=min_reviews,
                           min_age_days=max(age[0] * 30, 30), max_age_months=age[1],
                           include_free=include_free, exclude_publisher=[])
    st.session_state["settings"] = {"etiket": tag, **vars(args), "top": top}
    try:
        with st.status(f"{tag} analiz ediliyor", expanded=True) as status:
            st.write("SteamSpy'dan etiket listesi alınıyor")
            spy_rows, appids = bs.tag_candidates(F, tag, max_price, min_reviews, top)
            if not appids:
                status.update(label="Bu etikette uygun oyun bulunamadı", state="error")
                F.record_error("etiket", f"'{tag}' etiketi boş döndü veya filtrelerden oyun geçmedi "
                               f"(SteamSpy satır sayısı: {len(spy_rows)})", severity="engelleyici")
                st.session_state.pop("games", None)
            else:
                st.write(f"Etikette {tr_num(len(spy_rows))} oyun var. Yeniden eskiye en fazla "
                         f"{len(appids)} aday taranacak, pencereye girenlerin en başarılı "
                         f"{top} tanesi seçilecek.")
                bar = st.progress(0.0)
                games, skipped, coverage = bs.collect(
                    F, appids, spy_rows, args, YT_KEY, top=top,
                    on_progress=lambda ph, i, n, x: bar.progress(i / n, text=f"{ph}: {i}/{n}, {x}"))
                st.session_state["coverage"] = coverage
                if not games and skipped.get("ağ hatası"):
                    F.record_error("analiz", f"İncelenen {len(appids)} oyunun hiçbirinden veri "
                                   f"alınamadı. Elenenler: {skipped}", severity="engelleyici")
                    status.update(label="Veri alınamadı", state="error")
                else:
                    status.update(label=f"{len(games)} oyun puanlandı", state="complete",
                                  expanded=False)
                st.session_state.update(games=games, skipped=skipped, tag=tag)
                st.rerun()
    except Exception as e:
        blocking_error("analiz", e, etiket=tag)
        st.session_state.pop("games", None)

if any(e["onem"] == "engelleyici" for e in F.errors):
    st.error("Analiz durdu. Sayfanın altındaki tanı raporunu kopyalayıp Claude'a gönderin.", icon="⛔")

# ----------------------------------------------------------------- boş durum
games = st.session_state.get("games")
if not games:
    if games is not None:
        msg = ("Filtrelerden geçen oyun çıkmadı. Yaş aralığını genişletin, fiyat sınırını artırın "
               "ya da en az yorum sayısını düşürün.")
        st.warning(msg + f" Elenenler: {st.session_state.get('skipped')}")
    st.markdown(
        "<div class='rd-empty'><h2>Bir tür seçin, radar taramaya başlasın</h2>"
        "<p>Etiketi yazıp Analiz et'e basın ya da aşağıdan bir örnekle başlayın. İlk tarama "
        "100 oyun için 10–15 dakika sürer; aynı gün içindeki tekrarlar önbellekten saniyeler "
        "içinde gelir.</p></div>", unsafe_allow_html=True)
    ex = st.columns(len(EXAMPLE_TAGS) + 3)
    for i, t in enumerate(EXAMPLE_TAGS):
        ex[i].button(t, on_click=pick_tag, args=(t,), width="stretch")
    render_footer()
    st.stop()

if sum(weights.values()) == 0:
    st.warning("Puanlama için en az bir ağırlık sıfırdan büyük olmalı. Ağırlıklar menüsünden açın.")
    render_footer()
    st.stop()

# ------------------------------------------------------------------ sonuçlar
bs.score_all(games, weights)
df = pd.DataFrame([{
    "Skor": g["skor"], "Oyun": g["ad"], "Ay": g["yas_ay"], "Fiyat": g["fiyat_usd"],
    "Yorum": g["toplam_yorum"], "% Pozitif": g["pozitif_yuzde"], "Son 30 gün": g["son30_yorum"],
    "Zirve oyuncu": g["zirve_ccu_dun"], "Tahmini satış": g["tahmini_satis"],
} for g in games])

cov = st.session_state.get("coverage") or {}
if cov and not cov.get("pencere_tamamen_tarandi"):
    st.warning(f"Tarama sınırına ulaşıldı: {cov.get('taranan')} aday tarandı, en eskisi "
               f"{cov.get('taranan_en_eski_cikis')} tarihli. Seçtiğiniz pencerenin daha eski kısmı "
               f"incelenmedi; daha dar bir etiket seçin ya da oyun sayısını artırın.")

oldest = min(g["cikis"] for g in games)
kpis = [(tr_num(len(df)), "oyun puanlandı"),
        (f"%{df['% Pozitif'].median():.0f}", "medyan pozitif yorum"),
        (f"${df['Fiyat'].median():.2f}", "medyan fiyat"),
        (tr_num(df["Tahmini satış"].median()), "medyan tahmini satış"),
        (dt.date.fromisoformat(oldest).strftime("%b %Y"), "en eski oyunun çıkışı")]
st.markdown("<div class='rd-kpis'>" + "".join(
    f"<div class='rd-kpi'><b>{v}</b><small>{k}</small></div>" for v, k in kpis) + "</div>",
    unsafe_allow_html=True)

left, right = st.columns([7, 5], gap="large")
with left:
    h1, h2, h3 = st.columns([3, 1, 1], vertical_alignment="center")
    h1.markdown("<div class='rd-section'>Sıralama</div><p class='rd-hint'>Bir satıra tıklayın, "
                "oyunun ayrıntısı sağda açılsın.</p>", unsafe_allow_html=True)
    h2.download_button("CSV indir", df.to_csv(index=False).encode("utf-8-sig"),
                       f"sonuclar_{st.session_state['tag']}.csv", "text/csv", width="stretch")
    h3.download_button("Ajan JSON", json.dumps(
        {"etiket": st.session_state["tag"], "agirliklar": weights, "kapsam": cov,
         "elenenler": st.session_state["skipped"], "oyunlar": games}, ensure_ascii=False, indent=2),
        f"sonuclar_{st.session_state['tag']}.json", "application/json", width="stretch",
        help="Analist ajanın okuyacağı dosya")
    event = st.dataframe(
        df, hide_index=True, width="stretch", height=560, key="table",
        on_select="rerun", selection_mode="single-row",
        column_config={
            "Skor": st.column_config.ProgressColumn("Skor", min_value=0, max_value=100,
                                                    format="%.1f", width="small"),
            "Oyun": st.column_config.TextColumn(width="medium"),
            "Fiyat": st.column_config.NumberColumn(format="$%.2f"),
            "Ay": st.column_config.NumberColumn(format="%.1f"),
            "Tahmini satış": st.column_config.NumberColumn(format="localized"),
        })
    try:
        rows = event.selection.rows
    except Exception:
        rows = []
    sel = rows[0] if rows else 0

with right:
    g = games[sel]
    color = heat(g["skor"])
    bars = ""
    for m, w in weights.items():
        if w <= 0:
            continue
        pts = g["puan_detay"].get(m)
        if pts is None:
            bars += (f"<div class='rd-bar'><span>{METRIC_LABELS[m]}</span><div class='track'></div>"
                     f"<em>ölçülmedi</em></div>")
            continue
        pct = pts / w
        bars += (f"<div class='rd-bar'><span>{METRIC_LABELS[m]}</span><div class='track'>"
                 f"<div class='fill' style='width:{pct * 100:.0f}%;background:{heat(pct * 100)}'>"
                 f"</div></div><em>{pts:.1f} / {w}</em></div>")
    facts = [(f"{g['toplam_yorum']:,}".replace(",", "."), "yorum"),
             (f"%{g['pozitif_yuzde']}", "pozitif"),
             (f"${g['fiyat_usd']}", "fiyat"),
             (tr_num(g["son30_yorum"]) + ("+" if g.get("son30_alt_sinir") else ""), "son 30 gün yorum"),
             (tr_num(g["zirve_ccu_dun"] or 0), "dünkü zirve oyuncu"),
             (tr_num(g["tahmini_satis"]), "tahmini satış")]
    st.markdown(
        f"<div class='rd-game'>"
        f"<div class='rd-score' style='color:{color}'>{g['skor']:.0f}<small>/ 100</small></div>"
        f"<div class='rd-name'>{html.escape(g['ad'])}</div>"
        f"<div class='rd-meta'>{html.escape(g['gelistirici'])}, {g['cikis']} "
        f"({g['yas_ay']:.0f} ay önce). "
        f"<a href='https://store.steampowered.com/app/{g['appid']}' target='_blank'>Steam sayfası</a>"
        f"</div><div class='rd-facts'>"
        + "".join(f"<div><b>{v}</b><small>{k}</small></div>" for v, k in facts)
        + f"</div>{bars}</div>", unsafe_allow_html=True)

st.markdown("<div style='height:1.4rem'></div>", unsafe_allow_html=True)
left2, right2 = st.columns([7, 5], gap="large")
heat_scale = alt.Scale(domain=[0, 50, 100], range=HEAT)
with left2:
    st.markdown("<div class='rd-section'>Yaş ve yorum dağılımı</div><p class='rd-hint'>Sol üstteki "
                "oyunlar kısa sürede çok yorum toplamış olanlar.</p>", unsafe_allow_html=True)
    scatter = alt.Chart(df).mark_circle(opacity=.85, stroke=INK, strokeWidth=1).encode(
        x=alt.X("Ay:Q", title="Çıkıştan beri geçen ay"),
        y=alt.Y("Yorum:Q", scale=alt.Scale(type="log"), title="Toplam yorum (log)"),
        size=alt.Size("Skor:Q", scale=alt.Scale(range=[30, 420]), legend=None),
        color=alt.Color("Skor:Q", scale=heat_scale, legend=None),
        tooltip=["Oyun", "Skor", "Yorum", "% Pozitif", alt.Tooltip("Fiyat", format="$.2f")],
    ).properties(height=320)
    st.altair_chart(chart_theme(scatter), width="stretch")
with right2:
    st.markdown("<div class='rd-section'>Fiyat bandına göre skor</div><p class='rd-hint'>Çubuk "
                "üstündeki sayı o banttaki oyun sayısı.</p>", unsafe_allow_html=True)
    df["Bant"] = pd.cut(df["Fiyat"], [0, 5, 10, 15, 20, 30, 1000], include_lowest=True,
                        labels=["0–5$", "5–10$", "10–15$", "15–20$", "20–30$", "30$+"])
    band = (df.groupby("Bant", observed=True)
            .agg(Skor=("Skor", "mean"), Adet=("Skor", "size")).reset_index())
    base = alt.Chart(band).encode(x=alt.X("Bant:N", sort=None, title=None,
                                          axis=alt.Axis(labelAngle=0)))
    bars_c = base.mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5).encode(
        y=alt.Y("Skor:Q", title="Ortalama skor", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("Skor:Q", scale=heat_scale, legend=None),
        tooltip=["Bant", alt.Tooltip("Skor", format=".1f"), "Adet"])
    labels = base.mark_text(dy=-8, color=MUTED, fontSize=11).encode(
        y="Skor:Q", text="Adet:Q")
    st.altair_chart(chart_theme((bars_c + labels).properties(height=320)), width="stretch")

render_footer()
