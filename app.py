"""
International Cost of Capital App
=================================
Compares a project's cost of equity, cost of debt and WACC in USD and in the
currency of a selected country.

Data sources (fetched live, cached, refreshable from the sidebar):
  * Industry unlevered betas ........ Damodaran, betas.xls (U.S. industries)
  * BBB (Baa2) default spread ....... Damodaran, ratings.html (large non-financial firms)
  * Statutory corporate tax rates ... OECD Corporate Tax Statistics (combined CIT rate)
  * 10-year government bond yields .. Trading Economics

Run locally:  streamlit run app.py
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 6.5          # %
DEFAULT_DEBT_SHARE = 30.0  # % of firm value (D / (D + E))

BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URLS = [
    "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html",
    "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html",
]
OECD_PAGE_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb"
)
# Official OECD Data Explorer (SDMX) API serving the same Corporate Tax Statistics
# dataset that the publication page above is built from.
OECD_API_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?startPeriod={start}&format=csvfilewithlabels"
)
TE_BONDS_URL = "https://tradingeconomics.com/bonds"
TE_COUNTRY_URL = "https://tradingeconomics.com/{slug}/government-bond-yield"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 25

# name -> (ISO3 for OECD, Trading Economics slug, currency code)
COUNTRIES: dict[str, tuple[str, str, str]] = {
    "United States": ("USA", "united-states", "USD"),
    "Australia": ("AUS", "australia", "AUD"),
    "Austria": ("AUT", "austria", "EUR"),
    "Belgium": ("BEL", "belgium", "EUR"),
    "Brazil": ("BRA", "brazil", "BRL"),
    "Canada": ("CAN", "canada", "CAD"),
    "Chile": ("CHL", "chile", "CLP"),
    "China": ("CHN", "china", "CNY"),
    "Colombia": ("COL", "colombia", "COP"),
    "Croatia": ("HRV", "croatia", "EUR"),
    "Czech Republic": ("CZE", "czech-republic", "CZK"),
    "Denmark": ("DNK", "denmark", "DKK"),
    "Finland": ("FIN", "finland", "EUR"),
    "France": ("FRA", "france", "EUR"),
    "Germany": ("DEU", "germany", "EUR"),
    "Greece": ("GRC", "greece", "EUR"),
    "Hong Kong": ("HKG", "hong-kong", "HKD"),
    "Hungary": ("HUN", "hungary", "HUF"),
    "Iceland": ("ISL", "iceland", "ISK"),
    "India": ("IND", "india", "INR"),
    "Indonesia": ("IDN", "indonesia", "IDR"),
    "Ireland": ("IRL", "ireland", "EUR"),
    "Israel": ("ISR", "israel", "ILS"),
    "Italy": ("ITA", "italy", "EUR"),
    "Japan": ("JPN", "japan", "JPY"),
    "Kenya": ("KEN", "kenya", "KES"),
    "Lithuania": ("LTU", "lithuania", "EUR"),
    "Malaysia": ("MYS", "malaysia", "MYR"),
    "Mexico": ("MEX", "mexico", "MXN"),
    "Netherlands": ("NLD", "netherlands", "EUR"),
    "New Zealand": ("NZL", "new-zealand", "NZD"),
    "Nigeria": ("NGA", "nigeria", "NGN"),
    "Norway": ("NOR", "norway", "NOK"),
    "Pakistan": ("PAK", "pakistan", "PKR"),
    "Philippines": ("PHL", "philippines", "PHP"),
    "Poland": ("POL", "poland", "PLN"),
    "Portugal": ("PRT", "portugal", "EUR"),
    "Romania": ("ROU", "romania", "RON"),
    "Singapore": ("SGP", "singapore", "SGD"),
    "Slovakia": ("SVK", "slovakia", "EUR"),
    "Slovenia": ("SVN", "slovenia", "EUR"),
    "South Africa": ("ZAF", "south-africa", "ZAR"),
    "South Korea": ("KOR", "south-korea", "KRW"),
    "Spain": ("ESP", "spain", "EUR"),
    "Sweden": ("SWE", "sweden", "SEK"),
    "Switzerland": ("CHE", "switzerland", "CHF"),
    "Thailand": ("THA", "thailand", "THB"),
    "Turkey": ("TUR", "turkey", "TRY"),
    "United Kingdom": ("GBR", "united-kingdom", "GBP"),
    "Vietnam": ("VNM", "vietnam", "VND"),
}

# Last-resort snapshots, used ONLY if a live fetch fails (clearly flagged in the app).
# Tax: OECD combined statutory CIT rate (%), latest year available, fetched 2026-11-16.
FALLBACK_TAX = {
    "USA": (25.53, 2026), "AUS": (30.0, 2026), "AUT": (23.0, 2026), "BEL": (25.0, 2026),
    "BRA": (34.0, 2026), "CAN": (26.0, 2026), "CHL": (27.0, 2026), "CHN": (25.0, 2026),
    "COL": (35.0, 2025), "HRV": (18.0, 2026), "CZE": (21.0, 2026), "DNK": (22.0, 2026),
    "FIN": (20.0, 2026), "FRA": (36.13, 2026), "DEU": (30.13, 2026), "GRC": (22.0, 2026),
    "HKG": (16.5, 2026), "HUN": (9.0, 2026), "ISL": (20.0, 2026), "IND": (25.17, 2026),
    "IDN": (22.0, 2026), "IRL": (12.5, 2026), "ISR": (23.0, 2025), "ITA": (27.81, 2026),
    "JPN": (29.74, 2026), "KEN": (30.0, 2026), "LTU": (17.0, 2026), "MYS": (24.0, 2026),
    "MEX": (30.0, 2026), "NLD": (25.8, 2026), "NZL": (28.0, 2026), "NGA": (30.0, 2026),
    "NOR": (22.0, 2026), "PAK": (29.0, 2026), "PHL": (25.0, 2026), "POL": (19.0, 2026),
    "PRT": (29.5, 2026), "ROU": (16.0, 2026), "SGP": (17.0, 2026), "SVK": (24.0, 2026),
    "SVN": (22.0, 2026), "ZAF": (27.0, 2026), "KOR": (27.5, 2026), "ESP": (25.0, 2026),
    "SWE": (20.6, 2026), "CHE": (19.47, 2026), "THA": (20.0, 2026), "TUR": (25.0, 2025),
    "GBR": (25.0, 2026), "VNM": (20.0, 2026),
}
# Yields: Trading Economics 10Y yield (%), snapshot 2026-11-16.
FALLBACK_YIELDS = {
    "united-states": 5.14, "united-kingdom": 5.35, "germany": 3.38, "france": 4.95,
    "japan": 2.89, "canada": 3.82, "australia": 5.13, "italy": 4.46, "spain": 3.96,
    "netherlands": 3.48, "switzerland": 0.57, "sweden": 3.10, "norway": 4.39,
    "denmark": 3.17, "finland": 3.76, "belgium": 4.39, "austria": 3.63, "ireland": 3.52,
    "portugal": 3.84, "greece": 4.30, "poland": 6.34, "czech-republic": 5.23,
    "hungary": 5.53, "new-zealand": 4.77, "south-korea": 3.99, "mexico": 9.30,
    "brazil": 15.35, "chile": 6.01, "colombia": 12.70, "china": 1.63, "india": 7.32,
    "indonesia": 7.61, "malaysia": 3.87, "philippines": 7.70, "thailand": 1.98,
    "vietnam": 4.57, "singapore": 2.34, "hong-kong": 4.00, "south-africa": 8.72,
    "turkey": 36.48, "israel": 4.13, "kenya": 12.50, "nigeria": 16.60, "romania": 7.16,
    "iceland": 6.81, "slovakia": 4.30, "slovenia": 3.94, "lithuania": 4.34,
    "croatia": 3.87, "pakistan": 12.62,
}
FALLBACK_BBB = (1.11, "January 2026")


# ---------------------------------------------------------------------------
# Core finance (pure functions, all rates as decimals: 0.05 = 5%)
# ---------------------------------------------------------------------------

def levered_beta(beta_u: float, debt_share: float, tax: float) -> float:
    """Hamada: beta_L = beta_U * [1 + (1 - t) * D/E], with D/E = w_d / (1 - w_d)."""
    if debt_share >= 1:
        raise ValueError("Debt share must be below 100%.")
    de = debt_share / (1 - debt_share)
    return beta_u * (1 + (1 - tax) * de)


def convert_rate(rate_usd: float, rf_us: float, rf_fc: float, method: str = "multiplicative") -> float:
    """Translate a USD rate into a foreign-currency rate using the
    government-yield differential as expected inflation/depreciation
    (international Fisher effect / interest-rate parity).

    multiplicative: (1 + r_FC) = (1 + r_USD) * (1 + rf_FC) / (1 + rf_US)
    additive      :  r_FC      =  r_USD + (rf_FC - rf_US)
    """
    if method == "additive":
        return rate_usd + (rf_fc - rf_us)
    return (1 + rate_usd) * (1 + rf_fc) / (1 + rf_us) - 1


def auto_fc_spread(spread_usd: float, rf_us: float, rf_fc: float, method: str = "multiplicative") -> float:
    """Foreign-currency BBB spread implied by converting the USD pre-tax cost of
    debt (rf_US + spread) into foreign currency and subtracting rf_FC."""
    kd_fc = convert_rate(rf_us + spread_usd, rf_us, rf_fc, method)
    return kd_fc - rf_fc  # multiplicative => spread_usd * (1 + rf_FC)/(1 + rf_US)


def compute(
    beta_u: float,
    rf_us: float,
    rf_fc: float,
    erp: float,
    debt_share: float,
    tax: float,
    spread_usd: float,
    spread_fc_override: float | None = None,
    method: str = "multiplicative",
) -> dict:
    """Return all intermediate and final numbers for USD and foreign currency."""
    w_d = debt_share
    w_e = 1 - w_d
    beta_l = levered_beta(beta_u, w_d, tax)

    # ---- USD
    ke_usd = rf_us + beta_l * erp
    kd_usd = rf_us + spread_usd
    wacc_usd = w_e * ke_usd + w_d * kd_usd * (1 - tax)

    # ---- Foreign currency
    ke_fc = convert_rate(ke_usd, rf_us, rf_fc, method)
    spread_fc_auto = auto_fc_spread(spread_usd, rf_us, rf_fc, method)
    spread_fc = spread_fc_auto if spread_fc_override is None else spread_fc_override
    kd_fc = rf_fc + spread_fc
    wacc_fc = w_e * ke_fc + w_d * kd_fc * (1 - tax)

    return dict(
        beta_u=beta_u, beta_l=beta_l, de_ratio=w_d / w_e, w_d=w_d, w_e=w_e, tax=tax, erp=erp,
        rf_us=rf_us, rf_fc=rf_fc,
        ke_usd=ke_usd, spread_usd=spread_usd, kd_usd=kd_usd,
        kd_usd_at=kd_usd * (1 - tax), wacc_usd=wacc_usd,
        ke_fc=ke_fc, spread_fc_auto=spread_fc_auto, spread_fc=spread_fc,
        spread_fc_overridden=spread_fc_override is not None, kd_fc=kd_fc,
        kd_fc_at=kd_fc * (1 - tax), wacc_fc=wacc_fc,
    )


# ---------------------------------------------------------------------------
# Data fetching (pure functions; Streamlit caching is applied in the UI layer)
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _get(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def fetch_betas() -> tuple[pd.DataFrame, str]:
    """Damodaran U.S. industry betas. Returns (table, 'date updated')."""
    raw = pd.read_excel(io.BytesIO(_get(BETAS_URL).content), sheet_name="Industry Averages", header=None)
    first = raw.iloc[:, 0].astype(str).str.strip()
    hdr = first[first == "Industry Name"].index[0]
    date_row = first[first.str.startswith("Date updated")].index
    as_of = "unknown"
    if len(date_row):
        v = raw.iloc[date_row[0], 1]
        as_of = v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)
    df = raw.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[hdr]]
    df = df.rename(columns={"Industry Name": "Industry"})
    keep = ["Industry", "Number of firms", "Beta", "D/E Ratio", "Effective Tax rate", "Unlevered beta"]
    df = df[keep].dropna(subset=["Industry", "Unlevered beta"])
    df = df[~df["Industry"].astype(str).str.startswith("Total Market")]
    for c in keep[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.reset_index(drop=True), as_of


def fetch_bbb_spread() -> tuple[float, str, str]:
    """Damodaran Baa2/BBB spread for large non-financial firms.
    Returns (spread %, 'as of' text, url used)."""
    last_err = None
    for url in RATINGS_URLS:
        try:
            r = _get(url)
            html = r.content.decode("latin-1", errors="ignore")
            tbl = pd.read_html(io.StringIO(html))[0]
            # Left block (columns 0-3) = large non-financial service firms.
            for _, row in tbl.iterrows():
                if str(row.iloc[2]).strip() == "Baa2/BBB":
                    spread = float(str(row.iloc[3]).replace("%", "").strip())
                    m = re.search(r"Data used is as of\s+([A-Za-z]+\s+\d{4})", html)
                    return spread, (m.group(1) if m else "unknown"), url
            raise ValueError("Baa2/BBB row not found")
        except Exception as e:  # try next URL
            last_err = e
    raise RuntimeError(f"Could not read Damodaran ratings page: {last_err}")


def fetch_tax_rates() -> dict[str, tuple[float, int]]:
    """OECD combined (central + sub-central) statutory CIT rate, latest year per country.
    Returns {ISO3: (rate %, year)}."""
    start = datetime.now().year - 5
    df = pd.read_csv(io.StringIO(_get(OECD_API_URL.format(start=start)).text))
    df = df[(df["MEASURE"] == "CIT_C") & (df["TARGETING"] == "ST")].dropna(subset=["OBS_VALUE"])
    df = df.sort_values("TIME_PERIOD").groupby("REF_AREA").tail(1)
    return {r.REF_AREA: (float(r.OBS_VALUE), int(r.TIME_PERIOD)) for r in df.itertuples()}


def fetch_te_overview() -> dict[str, float]:
    """Try the Trading Economics /bonds overview table: {country name: 10Y yield %}.
    Returns {} if the page carries no data table (e.g. an interstitial page)."""
    try:
        tables = pd.read_html(io.StringIO(_get(TE_BONDS_URL).text))
    except Exception:
        return {}
    out: dict[str, float] = {}
    for t in tables:
        cols = [str(c) for c in t.columns]
        if "Yield" not in cols:
            continue
        for _, row in t.iterrows():
            try:
                out[str(row.iloc[0]).strip()] = float(row["Yield"])
            except (TypeError, ValueError):
                pass
    return out


def fetch_te_country_yield(slug: str) -> tuple[float, str]:
    """10Y yield from the Trading Economics country page. Returns (yield %, 'Mon/DD')."""
    tables = pd.read_html(io.StringIO(_get(TE_COUNTRY_URL.format(slug=slug)).text))
    t = tables[0]
    row = t[t.iloc[:, 0].astype(str).str.contains(r"\b10Y\b", regex=True)]
    if row.empty:
        raise ValueError("10Y row not found")
    date = str(row.iloc[0]["Date"]) if "Date" in t.columns else ""
    return float(row.iloc[0, 1]), date


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

def run_app() -> None:  # noqa: C901 - UI function is long by nature
    import streamlit as st

    st.set_page_config(page_title="International Cost of Capital", page_icon="🌍", layout="wide")

    # ---------- cached loaders (each returns a dict incl. status for the Sources tab)
    @st.cache_data(ttl=6 * 3600, show_spinner="Fetching Damodaran industry betas…")
    def load_betas():
        df, as_of = fetch_betas()
        return dict(df=df, as_of=as_of, fetched=_now())

    @st.cache_data(ttl=6 * 3600, show_spinner="Fetching Damodaran BBB spread…")
    def load_bbb():
        try:
            s, as_of, url = fetch_bbb_spread()
            return dict(value=s, as_of=as_of, url=url, status="Live", fetched=_now())
        except Exception as e:
            return dict(value=FALLBACK_BBB[0], as_of=FALLBACK_BBB[1], url=RATINGS_URLS[0],
                        status=f"Fallback snapshot (fetch failed: {e})", fetched=_now())

    @st.cache_data(ttl=6 * 3600, show_spinner="Fetching OECD corporate tax rates…")
    def load_tax():
        try:
            return dict(rates=fetch_tax_rates(), status="Live", fetched=_now())
        except Exception as e:
            return dict(rates=dict(FALLBACK_TAX), status=f"Fallback snapshot (fetch failed: {e})", fetched=_now())

    @st.cache_data(ttl=3600, show_spinner=False)
    def load_te_overview():
        return fetch_te_overview()

    @st.cache_data(ttl=3600, show_spinner="Fetching 10-year government bond yield…")
    def load_yield(country: str):
        _, slug, _ = COUNTRIES[country]
        overview = load_te_overview()
        if country in overview:
            return dict(value=overview[country], as_of=datetime.now().strftime("%b/%d"),
                        url=TE_BONDS_URL, status="Live", fetched=_now())
        try:
            y, d = fetch_te_country_yield(slug)
            return dict(value=y, as_of=d, url=TE_COUNTRY_URL.format(slug=slug), status="Live", fetched=_now())
        except Exception as e:
            return dict(value=FALLBACK_YIELDS[slug], as_of="2026-11-16 snapshot",
                        url=TE_COUNTRY_URL.format(slug=slug),
                        status=f"Fallback snapshot (fetch failed: {e})", fetched=_now())

    # ---------- header
    st.title("🌍 International Cost of Capital")
    st.caption(
        "Cost of equity, cost of debt and WACC for a project in **USD** and in the **currency of a "
        "selected country**, built from Damodaran betas & spreads, OECD tax rates and Trading Economics yields."
    )

    # ---------- sidebar: data refresh + choices
    sb = st.sidebar
    if sb.button("🔄 Refresh data fetch", width="stretch",
                 help="Clear the cache and re-download all source data."):
        st.cache_data.clear()
        st.rerun()

    try:
        betas = load_betas()
    except Exception as e:
        st.error(f"Could not load Damodaran betas ({e}). Use the manual unlevered-beta override.")
        betas = dict(df=pd.DataFrame({"Industry": [DEFAULT_INDUSTRY], "Number of firms": [None],
                                      "Beta": [None], "D/E Ratio": [None],
                                      "Effective Tax rate": [None], "Unlevered beta": [1.0]}),
                     as_of="unavailable", fetched=_now())
    bdf = betas["df"]
    bbb = load_bbb()
    tax = load_tax()

    sb.header("Project settings")
    industries = bdf["Industry"].tolist()
    industry = sb.selectbox("Industry (Damodaran, U.S.)", industries,
                            index=industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0)
    names = list(COUNTRIES)
    country = sb.selectbox("Target country", names, index=names.index(DEFAULT_COUNTRY))
    iso_fc, slug_fc, ccy = COUNTRIES[country]
    fcl = ccy if ccy != "USD" else "USD (selected country)"  # foreign-column label, never equal to "USD"
    erp_pct = sb.slider("Equity risk premium (%)", 0.0, 15.0, DEFAULT_ERP, 0.1)
    debt_pct = sb.slider("Target debt share D/(D+E) (%)", 0.0, 90.0, DEFAULT_DEBT_SHARE, 1.0)
    tax_choice = sb.radio("Project tax rate", ["U.S.", f"{country}"], index=0, horizontal=True,
                          help="The same tax rate is applied to both the USD and the foreign-currency calculation.")
    method_label = sb.radio(
        "Currency conversion", ["Multiplicative (exact parity)", "Additive (approximation)"], index=0,
        help="How USD rates are translated into foreign currency using the 10Y yield differential.")
    method = "multiplicative" if method_label.startswith("Multi") else "additive"

    # ---------- fetched values
    y_us = load_yield("United States")
    y_fc = load_yield(country) if country != "United States" else y_us
    tax_us_live = tax["rates"].get("USA", FALLBACK_TAX["USA"])
    tax_fc_live = tax["rates"].get(iso_fc, FALLBACK_TAX.get(iso_fc, (None, None)))
    beta_u_live = float(bdf.loc[bdf["Industry"] == industry, "Unlevered beta"].iloc[0])

    # ---------- sidebar: manual overrides
    sb.header("Manual overrides")
    sb.caption("Switch a toggle on to replace the fetched value.")

    def override(label: str, live: float, key: str, step: float = 0.01, fmt: str = "%.2f") -> float:
        on = sb.toggle(f"Override {label}", key=f"ov_{key}")
        if on:
            return sb.number_input(label, value=float(live), step=step, format=fmt, key=f"val_{key}")
        sb.caption(f"↳ fetched: **{fmt % live}**")
        return float(live)

    beta_u = override("unlevered beta", beta_u_live, "beta", 0.01, "%.4f")
    tax_us_pct = override("U.S. tax rate (%)", tax_us_live[0], "tax_us")
    if tax_fc_live[0] is None:
        sb.warning(f"No OECD rate for {country}; enter it manually.")
        tax_fc_live = (25.0, None)
    tax_fc_pct = override(f"{country} tax rate (%)", tax_fc_live[0], "tax_fc")
    spread_us_pct = override("USD BBB spread (%)", bbb["value"], "spread_us")
    rf_us_pct = override("U.S. 10Y yield (%)", y_us["value"], "rf_us")
    rf_fc_pct = override(f"{country} 10Y yield (%)", y_fc["value"], "rf_fc")

    # Foreign BBB spread: override or auto
    auto_spread_pct = 100 * auto_fc_spread(spread_us_pct / 100, rf_us_pct / 100, rf_fc_pct / 100, method)
    if method == "multiplicative":
        spread_rule = (f"{spread_us_pct:.2f}% × (1 + {rf_fc_pct:.2f}%) / (1 + {rf_us_pct:.2f}%) "
                       f"= **{auto_spread_pct:.4f}%**")
    else:
        spread_rule = f"USD spread (additive method) = **{auto_spread_pct:.4f}%**"
    sp_on = sb.toggle(f"Override {ccy} BBB spread (%)", key="ov_spread_fc",
                      help="Off: the USD BBB spread is translated into the foreign currency through the "
                           "10Y yield differential, so equal yields give an identical spread.")
    if sp_on:
        spread_fc_override = sb.number_input(f"{ccy} BBB spread (%)", value=round(auto_spread_pct, 4),
                                             step=0.01, format="%.4f", key="val_spread_fc") / 100
        sb.caption(f"↳ manual value in use · auto would be {spread_rule}")
    else:
        spread_fc_override = None
        sb.caption(f"↳ auto: {spread_rule}")

    project_tax_pct = tax_us_pct if tax_choice == "U.S." else tax_fc_pct

    # ---------- compute
    res = compute(beta_u=beta_u, rf_us=rf_us_pct / 100, rf_fc=rf_fc_pct / 100, erp=erp_pct / 100,
                  debt_share=debt_pct / 100, tax=project_tax_pct / 100, spread_usd=spread_us_pct / 100,
                  spread_fc_override=spread_fc_override, method=method)

    tab_res, tab_all, tab_src, tab_meth, tab_chk = st.tabs(
        ["📊 Results", "🏭 All industries", "🔗 Sources & dates", "📘 Method & assumptions", "✅ Verification checks"])

    # ======================= RESULTS =======================
    with tab_res:
        st.subheader(f"{industry} — USD vs {ccy} ({country})")
        st.caption(f"Project tax rate: **{project_tax_pct:.2f}%** ({tax_choice}) · ERP **{erp_pct:.2f}%** · "
                   f"Debt share **{debt_pct:.0f}%** · Conversion: **{method}**")
        for label, ku, kf in [
            ("Cost of equity", res["ke_usd"], res["ke_fc"]),
            ("Pre-tax cost of debt", res["kd_usd"], res["kd_fc"]),
            ("WACC", res["wacc_usd"], res["wacc_fc"]),
        ]:
            m1, m2 = st.columns(2)
            m1.metric(f"{label} — USD", f"{ku:.2%}")
            m2.metric(f"{label} — {fcl}", f"{kf:.2%}", delta=f"{(kf - ku) * 100:+.2f} pp vs USD",
                      delta_color="off")

        f = lambda x: f"{x:.4%}"  # noqa: E731
        table = pd.DataFrame(
            {
                "USD": [f(res["rf_us"]), f"{res['beta_u']:.4f}", f"{res['beta_l']:.4f}", f(res["erp"]),
                        f(res["ke_usd"]), f(res["spread_usd"]), f(res["kd_usd"]), f(res["tax"]),
                        f(res["kd_usd_at"]), f(res["w_e"]), f(res["w_d"]), f(res["wacc_usd"])],
                fcl: [f(res["rf_fc"]), f"{res['beta_u']:.4f}", f"{res['beta_l']:.4f}", f(res["erp"]),
                      f(res["ke_fc"]),
                      f(res["spread_fc"]) + (" (manual)" if res["spread_fc_overridden"] else " (auto)"),
                      f(res["kd_fc"]), f(res["tax"]), f(res["kd_fc_at"]), f(res["w_e"]), f(res["w_d"]),
                      f(res["wacc_fc"])],
            },
            index=["Risk-free rate (10Y govt.)", "Unlevered beta", "Levered beta", "Equity risk premium",
                   "Cost of equity", "BBB default spread", "Pre-tax cost of debt", "Project tax rate",
                   "After-tax cost of debt", "Equity weight E/V", "Debt weight D/V", "WACC"],
        )
        st.dataframe(table, width="stretch")
        with st.expander(f"How the {ccy} BBB spread is set", expanded=False):
            st.markdown(
                f"""
**Auto (default):** the USD pre-tax cost of debt is translated into {ccy} with the same parity factor used for
equity, then the {ccy} risk-free rate is subtracted. Algebraically this scales the USD spread:

`s_FC = s_USD × (1 + r_FC) / (1 + r_US)` → {spread_rule}

Why scale rather than copy the USD spread unchanged? Copying would make the {ccy} cost of debt inconsistent with the
{ccy} cost of equity (which *is* translated by the parity factor). With scaling, debt and equity are converted the same
way, and equal 10Y yields reproduce the USD spread exactly.

**Override:** switch on *Override {ccy} BBB spread* in the sidebar to enter a market-quoted local spread. This only
changes the {ccy} cost of debt and WACC — USD results are untouched (see the Verification checks tab).

Currently in use: **{res['spread_fc']:.4%}** ({'manual override' if res['spread_fc_overridden'] else 'auto'}).
"""
            )

        export = pd.DataFrame([{
            "Industry": industry, "Country": country, "Currency": ccy, "Conversion method": method,
            "Tax basis": tax_choice, "Project tax rate": res["tax"], "ERP": res["erp"], "Debt share": res["w_d"],
            "Unlevered beta": res["beta_u"], "Levered beta": res["beta_l"],
            "US 10Y": res["rf_us"], f"{fcl} 10Y": res["rf_fc"],
            "USD BBB spread": res["spread_usd"], f"{fcl} BBB spread": res["spread_fc"],
            f"{fcl} spread overridden": res["spread_fc_overridden"],
            "Cost of equity USD": res["ke_usd"], f"Cost of equity {fcl}": res["ke_fc"],
            "Pre-tax cost of debt USD": res["kd_usd"], f"Pre-tax cost of debt {fcl}": res["kd_fc"],
            "After-tax cost of debt USD": res["kd_usd_at"], f"After-tax cost of debt {fcl}": res["kd_fc_at"],
            "WACC USD": res["wacc_usd"], f"WACC {fcl}": res["wacc_fc"], "Generated": _now(),
        }]).T.rename(columns={0: "Value"})
        st.download_button("⬇️ Download these results (CSV)", export.to_csv().encode(),
                           file_name=f"cost_of_capital_{slug_fc}.csv", mime="text/csv")

    # ======================= ALL INDUSTRIES =======================
    with tab_all:
        st.subheader(f"All industries — {country} ({ccy}) vs USD, same settings")
        st.caption("Each industry's own Damodaran unlevered beta, with every other input as set in the sidebar "
                   "(the unlevered-beta override applies only to the selected industry).")
        rows = []
        for _, r in bdf.iterrows():
            bu = beta_u if r["Industry"] == industry else float(r["Unlevered beta"])
            x = compute(bu, res["rf_us"], res["rf_fc"], res["erp"], res["w_d"], res["tax"],
                        res["spread_usd"], spread_fc_override, method)
            rows.append({"Industry": r["Industry"], "Firms": r["Number of firms"], "Unlevered beta": bu,
                         "Levered beta": x["beta_l"], "Cost of equity USD": x["ke_usd"],
                         f"Cost of equity {fcl}": x["ke_fc"], "WACC USD": x["wacc_usd"],
                         f"WACC {fcl}": x["wacc_fc"]})
        all_df = pd.DataFrame(rows).sort_values("WACC USD", ascending=False).reset_index(drop=True)
        pct_cols = [c for c in all_df.columns if c.startswith(("Cost", "WACC"))]
        st.dataframe(
            all_df.style.format({**{c: "{:.2%}" for c in pct_cols}, "Unlevered beta": "{:.3f}",
                                 "Levered beta": "{:.3f}", "Firms": "{:.0f}"})
            .apply(lambda s: ["background-color: rgba(255,200,0,0.25)" if v == industry else "" for v in s],
                   subset=["Industry"]),
            width="stretch", height=480)
        chart = all_df.set_index("Industry")[["WACC USD", f"WACC {fcl}"]] * 100
        st.markdown("**WACC by industry (%)**")
        st.bar_chart(chart, height=420)
        st.download_button("⬇️ Download all-industry table (CSV)", all_df.to_csv(index=False).encode(),
                           file_name=f"all_industries_{slug_fc}.csv", mime="text/csv")

    # ======================= SOURCES =======================
    with tab_src:
        st.subheader("Data sources, dates and status")
        tax_fc_year = tax["rates"].get(iso_fc, (None, "n/a"))[1]
        src = pd.DataFrame([
            ["Unlevered beta — " + industry, f"{beta_u_live:.4f}", f"Updated {betas['as_of']}",
             "Live", BETAS_URL, betas["fetched"]],
            ["BBB (Baa2) spread, large non-financial firms", f"{bbb['value']:.2f}%", bbb["as_of"],
             bbb["status"], bbb["url"], bbb["fetched"]],
            ["Corporate tax rate — United States (OECD combined statutory)", f"{tax_us_live[0]:.2f}%",
             f"Tax year {tax['rates'].get('USA', FALLBACK_TAX['USA'])[1]}", tax["status"], OECD_PAGE_URL,
             tax["fetched"]],
            [f"Corporate tax rate — {country} (OECD combined statutory)", f"{tax_fc_live[0]:.2f}%",
             f"Tax year {tax_fc_year}", tax["status"], OECD_PAGE_URL, tax["fetched"]],
            ["10Y government yield — United States", f"{y_us['value']:.2f}%", y_us["as_of"], y_us["status"],
             y_us["url"], y_us["fetched"]],
            [f"10Y government yield — {country}", f"{y_fc['value']:.2f}%", y_fc["as_of"], y_fc["status"],
             y_fc["url"], y_fc["fetched"]],
        ], columns=["Item", "Fetched value", "Source date", "Status", "Source", "Fetched at"])
        st.dataframe(src, width="stretch", hide_index=True,
                     column_config={"Source": st.column_config.LinkColumn("Source")})
        st.markdown(
            f"""
- **Damodaran betas** — [{BETAS_URL}]({BETAS_URL}), sheet *Industry Averages*, column **Unlevered beta** (U.S. firms).
- **Damodaran ratings/spreads** — [ratings.html]({RATINGS_URLS[0]}), table *For large non-financial service firms*, row **Baa2/BBB**.
- **OECD corporate tax rates** — [Corporate Tax Statistics 2024, statutory CIT rates]({OECD_PAGE_URL}). The app reads the
  same dataset through the official [OECD Data Explorer API](https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all?format=csvfilewithlabels)
  (measure *Combined corporate income tax rate*, statutory), taking the latest year available for each country.
- **Trading Economics yields** — [tradingeconomics.com/bonds]({TE_BONDS_URL}); if the overview table is not served to the
  app, it reads the country page `tradingeconomics.com/<country>/government-bond-yield` (row **10Y**). The source date
  is Trading Economics' quote date (Mon/DD).
- Values marked *Fallback snapshot* mean the live fetch failed; a stored snapshot from 2026-11-16 is used instead.
  Use **Refresh data fetch** to retry, or the manual overrides.
"""
        )

    # ======================= METHOD =======================
    with tab_meth:
        st.subheader("Formulas")
        st.markdown("All rates in decimals. Subscript **US** = U.S. / USD, **FC** = foreign currency.")
        st.latex(r"\frac{D}{E} = \frac{w_d}{1-w_d},\qquad \beta_L = \beta_U\left[1 + (1-t)\frac{D}{E}\right]")
        st.markdown("**USD**")
        st.latex(r"k_{e}^{USD} = r_{US} + \beta_L \times ERP,\qquad k_{d}^{USD} = r_{US} + s_{BBB}")
        st.latex(r"WACC^{USD} = (1-w_d)\,k_e^{USD} + w_d\,k_d^{USD}(1-t)")
        st.markdown("**Foreign currency** (multiplicative / exact parity, default)")
        st.latex(r"k_e^{FC} = (1+k_e^{USD})\frac{1+r_{FC}}{1+r_{US}} - 1")
        st.latex(r"s^{FC}_{auto} = (1+r_{US}+s_{BBB})\frac{1+r_{FC}}{1+r_{US}} - 1 - r_{FC}"
                 r" = s_{BBB}\,\frac{1+r_{FC}}{1+r_{US}}")
        st.latex(r"k_d^{FC} = r_{FC} + s^{FC},\qquad WACC^{FC} = (1-w_d)\,k_e^{FC} + w_d\,k_d^{FC}(1-t)")
        st.markdown("*Additive approximation:* "
                    r"$k_e^{FC} = k_e^{USD} + (r_{FC}-r_{US})$ and $s^{FC}_{auto} = s_{BBB}$.")
        st.subheader("Main assumptions")
        st.markdown(
            """
1. **Risk-free rate** = 10-year local-currency government bond yield in each currency (no default-spread adjustment
   for sovereign risk).
2. **Beta** = Damodaran U.S. industry *unlevered* beta, relevered with the Hamada formula at the target debt share and
   the chosen project tax rate. The same business risk is assumed in both currencies.
3. **Equity risk premium** is the same (user-set) mature-market premium in both currencies; no separate country risk
   premium is added.
4. **Cost of debt** = risk-free rate + BBB (Baa2) default spread for large non-financial firms. The foreign-currency
   spread is, by default, the USD spread translated through the yield differential; it can be overridden.
5. **Currency translation**: the 10Y yield differential is treated as the expected inflation/depreciation differential
   (international Fisher effect / interest-rate parity), so a USD rate maps to a foreign rate by
   (1 + r_FC)/(1 + r_US).
6. **One project tax rate** (U.S. or selected country, switchable) is used for relevering beta and for the debt tax
   shield in *both* currencies, so differences between the columns come only from the currency translation.
7. Tax rate = OECD combined (central + sub-central) statutory corporate income tax rate, latest year available.
8. Capital structure weights are target market-value weights, identical in both currencies.
"""
        )

    # ======================= CHECKS =======================
    with tab_chk:
        st.subheader("Built-in verification checks (using your current settings)")
        base = dict(beta_u=beta_u, erp=res["erp"], debt_share=res["w_d"], tax=res["tax"],
                    spread_usd=res["spread_usd"], method=method)
        eq = compute(rf_us=res["rf_us"], rf_fc=res["rf_us"], spread_fc_override=None, **base)
        c1 = abs(eq["ke_usd"] - eq["ke_fc"]) < 1e-12 and abs(eq["wacc_usd"] - eq["wacc_fc"]) < 1e-12
        z = compute(rf_us=res["rf_us"], rf_fc=res["rf_fc"], spread_fc_override=spread_fc_override,
                    **{**base, "debt_share": 0.0})
        c2 = abs(z["wacc_usd"] - z["ke_usd"]) < 1e-12 and abs(z["wacc_fc"] - z["ke_fc"]) < 1e-12
        a = compute(rf_us=res["rf_us"], rf_fc=res["rf_fc"], spread_fc_override=None, **base)
        b = compute(rf_us=res["rf_us"], rf_fc=res["rf_fc"], spread_fc_override=a["spread_fc_auto"] + 0.02, **base)
        usd_keys = ["ke_usd", "kd_usd", "wacc_usd"]
        c3 = all(a[k] == b[k] for k in usd_keys) and b["kd_fc"] != a["kd_fc"]
        chk = pd.DataFrame([
            ["Equal 10Y yields (foreign = U.S.), spread override off → identical results",
             f"ke {eq['ke_usd']:.4%} vs {eq['ke_fc']:.4%}; WACC {eq['wacc_usd']:.4%} vs {eq['wacc_fc']:.4%}", c1],
            ["Zero debt → WACC equals cost of equity",
             f"USD {z['wacc_usd']:.4%} = {z['ke_usd']:.4%}; {ccy} {z['wacc_fc']:.4%} = {z['ke_fc']:.4%}", c2],
            [f"Overriding the {fcl} spread (+2 pp) leaves USD results unchanged",
             f"USD WACC {a['wacc_usd']:.4%} → {b['wacc_usd']:.4%}; {ccy} kd {a['kd_fc']:.4%} → {b['kd_fc']:.4%}, "
             f"WACC {a['wacc_fc']:.4%} → {b['wacc_fc']:.4%}",
             c3],
        ], columns=["Check", "Numbers", "Pass"])
        chk["Pass"] = chk["Pass"].map({True: "✅ Pass", False: "❌ Fail"})
        chk = chk[["Pass", "Check", "Numbers"]]
        st.dataframe(chk, width="stretch", hide_index=True)

        st.subheader("Step-by-step for the current selection (for checking by hand)")
        ke_fc_line = (
            f"(1 + {res['ke_usd']:.6f}) x (1 + {res['rf_fc']:.6f}) / (1 + {res['rf_us']:.6f}) - 1"
            if method == "multiplicative"
            else f"{res['ke_usd']:.6f} + ({res['rf_fc']:.6f} - {res['rf_us']:.6f})"
        )
        st.code(
            f"""D/E           = {res['w_d']:.4f} / {res['w_e']:.4f} = {res['de_ratio']:.6f}
beta_L         = {res['beta_u']:.6f} x [1 + (1 - {res['tax']:.6f}) x {res['de_ratio']:.6f}] = {res['beta_l']:.6f}
ke (USD)       = {res['rf_us']:.6f} + {res['beta_l']:.6f} x {res['erp']:.6f} = {res['ke_usd']:.6f}
kd (USD)       = {res['rf_us']:.6f} + {res['spread_usd']:.6f} = {res['kd_usd']:.6f}
WACC (USD)     = {res['w_e']:.4f} x {res['ke_usd']:.6f} + {res['w_d']:.4f} x {res['kd_usd']:.6f} x (1 - {res['tax']:.6f}) = {res['wacc_usd']:.6f}
ke ({ccy})       = {ke_fc_line} = {res['ke_fc']:.6f}   [{method}]
spread ({ccy})   = {res['spread_fc']:.6f}  ({'manual' if res['spread_fc_overridden'] else 'auto'})
kd ({ccy})       = {res['rf_fc']:.6f} + {res['spread_fc']:.6f} = {res['kd_fc']:.6f}
WACC ({ccy})     = {res['w_e']:.4f} x {res['ke_fc']:.6f} + {res['w_d']:.4f} x {res['kd_fc']:.6f} x (1 - {res['tax']:.6f}) = {res['wacc_fc']:.6f}""",
            language="text",
        )


if __name__ == "__main__":
    run_app()
