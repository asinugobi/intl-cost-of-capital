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

import html
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
# Streamlit UI (dark theme)
# ---------------------------------------------------------------------------

USD_COLOR = "#6AA8FF"
FC_COLOR = "#FFB35C"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&family=Space+Grotesk:wght@500;600&display=swap');
.stApp{background:#0A0C10;color:#E8EAF0;font-family:Inter,system-ui,sans-serif}
header[data-testid="stHeader"]{background:transparent}
.block-container,[data-testid="stMainBlockContainer"]{max-width:1440px;padding-top:1.4rem;padding-bottom:3rem}
h1,h2,h3{font-family:'Space Grotesk',Inter,sans-serif !important;letter-spacing:-0.01em}
[data-testid="stWidgetLabel"] p{font-size:12.5px !important;color:#98A2B3 !important}
div[data-baseweb="select"]>div{background:#0D1016 !important;border-color:#262C3A !important;border-radius:10px !important;min-height:44px}
div[data-baseweb="input"],div[data-baseweb="base-input"]{background:#0D1016 !important;border-color:#262C3A !important;border-radius:10px !important}
.stButton button,.stDownloadButton button{background:#171B24;border:1px solid #2E3546;border-radius:10px;min-height:42px;color:#E8EAF0;font-weight:500}
.stButton button:hover,.stDownloadButton button:hover{border-color:#6AA8FF;color:#FFFFFF}
.st-key-hdr{border-bottom:1px solid #1A1F2A;padding-bottom:12px;margin-bottom:8px}
.st-key-panel_project,.st-key-panel_assump,.st-key-panel_data{background:#11141B;border:1px solid #1F2430;border-radius:16px;padding:18px 18px 12px}
.st-key-panel_data [data-testid="stHorizontalBlock"]{border-top:1px solid #1A1F2A;padding-top:8px;align-items:center}
.st-key-taxb,.st-key-method{width:100% !important}
.st-key-taxb [data-testid="stButtonGroup"],.st-key-method [data-testid="stButtonGroup"]{width:100%}
.st-key-taxb [role="radiogroup"],.st-key-method [role="radiogroup"]{display:flex !important;width:100% !important;max-width:none !important;flex-wrap:nowrap !important;gap:4px;padding:4px;background:#0D1016;border:1px solid #262C3A;border-radius:12px}
.st-key-taxb button,.st-key-method button{flex:1 1 0;min-width:0;min-height:40px;border:0 !important;border-radius:9px !important;background:transparent;color:#98A2B3}
.st-key-taxb button[aria-checked="true"],.st-key-method button[aria-checked="true"]{background:#232A3A !important;color:#F2F4F8 !important}
.st-key-taxb button p,.st-key-method button p{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px}
.stTabs [role="tablist"]{gap:4px;background:#11141B;border:1px solid #1F2430;border-radius:14px;padding:6px;flex-wrap:wrap}
.stTabs [data-testid="stTab"]{min-height:40px;padding:0 14px;border-radius:9px;color:#98A2B3;display:flex;align-items:center}
.stTabs [data-testid="stTab"][aria-selected="true"]{background:#232A3A;color:#F2F4F8}
.stTabs [data-testid="stTab"] p{font-size:13.5px;font-weight:500}
.stTabs .react-aria-SelectionIndicator{display:none}
.stCode pre,[data-testid="stCode"] pre{background:#0D1016 !important;border:1px solid #1F2430;border-radius:12px}
@media (max-width:640px){
 .st-key-panel_data [data-testid="stHorizontalBlock"]{flex-wrap:nowrap !important}
 .st-key-panel_data [data-testid="stColumn"]{min-width:0 !important}
}
.coc-brand{display:flex;align-items:center;gap:12px}
.coc-logo{width:38px;height:38px;border-radius:10px;background:#141823;border:1px solid #262C3A;display:flex;align-items:center;justify-content:center;flex:none}
.coc-title{margin:0;font-family:'Space Grotesk',Inter,sans-serif;font-size:20px;font-weight:600;color:#E8EAF0;line-height:1.2}
.coc-sub{margin:2px 0 0;font-size:13px;color:#98A2B3}
.coc-pill{display:inline-flex;align-items:center;gap:8px;padding:9px 12px;border-radius:999px;background:#11141B;border:1px solid #232836;font-size:12.5px;color:#B4BCCB}
.coc-dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.coc-step{display:flex;align-items:center;gap:10px;margin:0 0 10px}
.coc-step b{width:24px;height:24px;border-radius:7px;background:#1B2232;color:#C9D6EE;font-size:12px;display:flex;align-items:center;justify-content:center}
.coc-step span{font-size:12.5px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:#C9D1E0}
.coc-hint{margin:-4px 0 4px;font-size:12px;color:#8A93A5}
.coc-lbl{font-size:13.5px;color:#E8EAF0;line-height:1.3}
.coc-src{font-size:11.5px;color:#8A93A5;margin-top:2px}
.coc-badge{font-size:10.5px;font-weight:600;letter-spacing:.03em;padding:2px 7px;border-radius:999px;margin-left:6px;white-space:nowrap}
.coc-b-live{background:#12301F;color:#7EE2A3}.coc-b-man{background:#3A2A12;color:#FFC67D}
.coc-b-auto{background:#1B2440;color:#A9C6FF}.coc-b-snap{background:#3A1518;color:#FF9AA2}
.coc-val{text-align:right;font-size:14px;font-weight:500;font-variant-numeric:tabular-nums}
.coc-note{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:11.5px;color:#B4BCCB;background:#0D1016;border:1px solid #1F2430;border-radius:8px;padding:7px 9px;margin:2px 0 8px;overflow-wrap:anywhere}
.coc-eyebrow{font-size:12.5px;color:#98A2B3;letter-spacing:.04em;text-transform:uppercase}
.coc-h2{margin:4px 0 0;font-family:'Space Grotesk',Inter,sans-serif;font-size:26px;font-weight:600;color:#E8EAF0}
.coc-chips{display:flex;gap:8px;flex-wrap:wrap}
.coc-chip{font-size:12.5px;padding:6px 10px;border-radius:999px;background:#141823;border:1px solid #232836;color:#C9D1E0;font-variant-numeric:tabular-nums}
.coc-head{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:14px}
.coc-cards{display:flex;flex-wrap:wrap;gap:16px}
.coc-card{flex:1 1 300px;min-width:0;position:relative;overflow:hidden;background:#11141B;border:1px solid #232836;border-radius:18px;padding:22px 22px 20px}
.coc-card .bar{position:absolute;left:0;right:0;top:0;height:3px}
.coc-card .top{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
.coc-card .ccy{font-family:'Space Grotesk',Inter,sans-serif;font-size:16px;font-weight:600;color:#E8EAF0}
.coc-card .sw{width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:8px}
.coc-card .csub{font-size:12.5px;color:#98A2B3;margin-left:8px}
.coc-card .delta{font-size:12px;font-weight:500;padding:4px 9px;border-radius:999px;background:#1A1F2B;color:#D7DDE8}
.coc-card .k{margin-top:18px;font-size:12px;color:#98A2B3;letter-spacing:.06em;text-transform:uppercase}
.coc-card .big{font-family:'Space Grotesk',Inter,sans-serif;font-size:54px;font-weight:600;line-height:1.05;letter-spacing:-.025em;color:#F2F4F8;font-variant-numeric:tabular-nums}
.coc-card .stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:18px;padding-top:16px;border-top:1px solid #1F2430}
.coc-card .stats div div:first-child{font-size:12px;color:#98A2B3}
.coc-card .stats div div:last-child{font-size:18px;font-weight:600;color:#E8EAF0;font-variant-numeric:tabular-nums;margin-top:2px}
.coc-insight{display:flex;gap:12px;align-items:flex-start;padding:14px 16px;background:#0F131B;border:1px solid #1F2430;border-radius:12px;margin:16px 0 6px;font-size:14px;color:#D7DDE8}
.coc-table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:14px}
.coc-table th{font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:#98A2B3;font-weight:500;text-align:right;padding:12px 6px;border-bottom:1px solid #232836}
.coc-table th:first-child,.coc-table td:first-child{text-align:left}
.coc-table td,.coc-table th{border-left:none !important;border-right:none !important;border-top:none !important;background:transparent !important}
.coc-table td{padding:10px 6px;border-bottom:1px solid #1A1F2A;text-align:right;color:#C9D1E0}
.coc-table tr.strong td{color:#F2F4F8;font-weight:600}
.coc-box{padding:16px;border-radius:12px;background:#0D1016;border:1px solid #1F2430;margin-top:16px;font-size:13px;color:#B4BCCB}
.coc-box h4{margin:0 0 6px;font-size:14px;font-weight:600;color:#E8EAF0;font-family:Inter,sans-serif}
.coc-mono{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:12.5px;color:#E8EAF0;overflow-wrap:anywhere}
.coc-legend{display:flex;gap:16px;font-size:12.5px;color:#B4BCCB;margin:4px 0 8px}
.coc-legend i{width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:6px;vertical-align:-1px}
.coc-ind{max-height:560px;overflow-y:auto;border:1px solid #1F2430;border-radius:12px}
.coc-irow{display:grid;grid-template-columns:minmax(150px,1.1fr) minmax(100px,1.3fr) 110px;gap:14px;align-items:center;padding:8px 14px;border-bottom:1px solid #1A1F2A;border-left:3px solid transparent}
.coc-irow.sel{background:#152036;border-left-color:#6AA8FF}
.coc-irow .n{min-width:0}.coc-irow .n span{display:block;font-size:13.5px;color:#E8EAF0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.coc-irow .n small{display:block;font-size:11.5px;color:#8A93A5}
.coc-irow .bars{display:flex;flex-direction:column;gap:4px}.coc-irow .bars i{display:block;height:7px;border-radius:4px}
.coc-irow .v{display:flex;flex-direction:column;align-items:flex-end;font-size:12.5px;font-variant-numeric:tabular-nums;color:#E8EAF0}
.coc-irow .v span:last-child{color:#B4BCCB}
.coc-check{display:flex;gap:14px;align-items:flex-start;padding:14px 16px;border-radius:12px;background:#0D1016;border:1px solid #1F2430;margin-bottom:10px}
.coc-check .name{font-size:14px;font-weight:500;color:#E8EAF0}.coc-check .det{font-size:12.5px;color:#B4BCCB;margin-top:3px;font-variant-numeric:tabular-nums}
.coc-src-row{padding:12px 14px;border-bottom:1px solid #1A1F2A;background:#0D1016}
.coc-src-row .t{display:flex;justify-content:space-between;gap:10px;font-size:13px;color:#E8EAF0}
.coc-src-row .t b{font-variant-numeric:tabular-nums}
.coc-src-row .d{font-size:12px;color:#8A93A5;margin-top:3px}
.coc-src-row a{color:#8DBBFF}
.coc-srcs{border:1px solid #1F2430;border-radius:12px;overflow:hidden}
.coc-ol{margin:0;padding-left:20px;font-size:13px;color:#C9D1E0}.coc-ol li{margin-bottom:6px}
.coc-foot{font-size:12px;color:#7C8596;margin-top:18px}
</style>
"""

GLOBE_SVG = ('<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#8DBBFF" stroke-width="1.6" '
             'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle>'
             '<path d="M3 12h18"></path><path d="M12 3a14 14 0 0 1 0 18"></path><path d="M12 3a14 14 0 0 0 0 18"></path></svg>')
INFO_SVG = ('<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#8DBBFF" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="flex:none;margin-top:1px">'
            '<circle cx="12" cy="12" r="9"></circle><path d="M12 11v5"></path><path d="M12 8h.01"></path></svg>')


def _e(x) -> str:
    return html.escape(str(x))


def _pct(x: float, d: int = 2) -> str:
    s = f"{x * 100:.{d}f}"
    return ("−" + s[1:] if s.startswith("-") else s) + "%"


def _pp(x: float) -> str:
    v = x * 100
    return ("+" if v >= 0 else "−") + f"{abs(v):.2f} pp"


def run_app() -> None:  # noqa: C901 - UI function is long by nature
    import streamlit as st

    st.set_page_config(page_title="International Cost of Capital", page_icon="🌍", layout="wide",
                       initial_sidebar_state="collapsed")
    st.markdown(CSS, unsafe_allow_html=True)
    ss = st.session_state

    def html_(s: str, where=None) -> None:
        (where or st).markdown(s, unsafe_allow_html=True)

    # ---------- cached loaders
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
                        status=f"Snapshot (fetch failed: {e})", fetched=_now())

    @st.cache_data(ttl=6 * 3600, show_spinner="Fetching OECD corporate tax rates…")
    def load_tax():
        try:
            return dict(rates=fetch_tax_rates(), status="Live", fetched=_now())
        except Exception as e:
            return dict(rates=dict(FALLBACK_TAX), status=f"Snapshot (fetch failed: {e})", fetched=_now())

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
                        status=f"Snapshot (fetch failed: {e})", fetched=_now())

    try:
        betas = load_betas()
        betas_status = "Live"
    except Exception as e:
        st.error(f"Could not load Damodaran betas ({e}). Use the manual unlevered-beta override.")
        betas = dict(df=pd.DataFrame({"Industry": [DEFAULT_INDUSTRY], "Number of firms": [None],
                                      "Beta": [None], "D/E Ratio": [None],
                                      "Effective Tax rate": [None], "Unlevered beta": [1.0]}),
                     as_of="unavailable", fetched=_now())
        betas_status = "Snapshot (fetch failed)"
    bdf = betas["df"]
    bbb = load_bbb()
    tax = load_tax()

    # ---------- header (status pill filled in once yields are known)
    with st.container(key="hdr"):
        h_title, h_pill, h_btn = st.columns([3.2, 2.2, 1], vertical_alignment="center")
        html_(f'<div class="coc-brand"><div class="coc-logo">{GLOBE_SVG}</div><div>'
              f'<p class="coc-title">International Cost of Capital</p>'
              f'<p class="coc-sub">Cost of equity, debt and WACC in USD vs. a local currency</p></div></div>', h_title)
        pill_slot = h_pill.empty()
        if h_btn.button("Refresh data", icon=":material/refresh:", use_container_width=True,
                        help="Clear the cache and re-download all source data."):
            st.cache_data.clear()
            st.rerun()

    left, right = st.columns([1, 2.2], gap="large")

    # ---------- helpers reading override state before widgets are drawn
    def eff(key: str, live: float) -> float:
        if ss.get(f"ov_{key}") and f"val_{key}" in ss:
            return float(ss[f"val_{key}"])
        return float(live)

    def step(n: int, title: str) -> str:
        return f'<div class="coc-step"><b>{n}</b><span>{_e(title)}</span></div>'

    # ======================= LEFT: INPUTS =======================
    with left:
        with st.container(key="panel_project"):
            html_(step(1, "Project"))
            industries = bdf["Industry"].tolist()
            industry = st.selectbox("Industry · Damodaran U.S.", industries, key="industry",
                                    index=industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0)
            names = list(COUNTRIES)
            country = st.selectbox("Target country", names, index=names.index(DEFAULT_COUNTRY), key="country",
                                   format_func=lambda n: f"{n} — {COUNTRIES[n][2]}")
        iso_fc, slug_fc, ccy = COUNTRIES[country]
        fcl = ccy if ccy != "USD" else "USD (selected)"
        is_us = country == "United States"
        c_name = "United States (selected)" if is_us else country

        y_us = load_yield("United States")
        y_fc = load_yield(country) if not is_us else y_us
        tax_us_live = tax["rates"].get("USA", FALLBACK_TAX["USA"])
        tax_fc_live = tax["rates"].get(iso_fc, FALLBACK_TAX.get(iso_fc, (25.0, None)))
        beta_u_live = float(bdf.loc[bdf["Industry"] == industry, "Unlevered beta"].iloc[0])

        with st.container(key="panel_assump"):
            html_(step(2, "Assumptions"))
            erp_pct = st.slider("Equity risk premium", 0.0, 15.0, DEFAULT_ERP, 0.1, format="%.1f%%", key="erp")
            debt_pct = st.slider("Target debt share D/(D+E)", 0.0, 90.0, DEFAULT_DEBT_SHARE, 1.0, format="%.0f%%",
                                 key="debt")
            t_us_now, t_fc_now = eff("tax_us", tax_us_live[0]), eff("tax_fc", tax_fc_live[0])
            st.segmented_control(
                "Project tax rate · used for both currencies", ["us", "fc"], default="us", key="taxb",
                format_func=lambda o: f"U.S. · {t_us_now:.2f}%" if o == "us" else f"{country} · {t_fc_now:.2f}%")
            st.segmented_control(
                "Currency conversion", ["multiplicative", "additive"], default="multiplicative", key="method",
                format_func=lambda o: "Exact parity" if o == "multiplicative" else "Additive (approx.)",
                help="How USD rates are translated into the local currency using the 10Y yield differential.")
            tax_basis = ss.get("taxb") or "us"
            method = ss.get("method") or "multiplicative"

        ov_keys = ["beta", "rf_us", "rf_fc", "tax_us", "tax_fc", "spread_us", "spread_fc"]

        def reset_overrides():
            for k in ov_keys:
                ss[f"ov_{k}"] = False

        with st.container(key="panel_data"):
            top_l, top_r = st.columns([2, 1], vertical_alignment="center")
            html_(step(3, "Market data"), top_l)
            if any(ss.get(f"ov_{k}") for k in ov_keys):
                top_r.button("Reset", on_click=reset_overrides, key="reset_ov", use_container_width=True,
                             help="Switch every override off")
            html_('<p class="coc-hint">Fetched automatically. Switch a row on to type your own value.</p>')

            def data_row(key, label, source, live, decimals, status="Live", unit="%", note=None):
                c1, c2, c3 = st.columns([2.2, 1.3, 0.62], vertical_alignment="center")
                on = c3.toggle(f"Override {label}", key=f"ov_{key}", label_visibility="collapsed")
                if on:
                    v = c2.number_input(label, value=round(float(live), decimals), step=0.01,
                                        format=f"%.{decimals}f", key=f"val_{key}", label_visibility="collapsed")
                else:
                    v = float(live)
                    html_(f'<div class="coc-val">{v:.{decimals}f}{unit}</div>', c2)
                if on:
                    badge, cls = "Manual", "coc-b-man"
                elif status == "Auto":
                    badge, cls = "Auto", "coc-b-auto"
                elif str(status).startswith("Live"):
                    badge, cls = "Live", "coc-b-live"
                else:
                    badge, cls = "Snapshot", "coc-b-snap"
                html_(f'<div class="coc-lbl">{_e(label)}<span class="coc-badge {cls}">{badge}</span></div>'
                      f'<div class="coc-src">{_e(source)}</div>', c1)
                return float(v), on

            beta_u, _ = data_row("beta", "Unlevered beta", f"Damodaran · updated {betas['as_of']}", beta_u_live, 4,
                                 betas_status, unit="")
            rf_us_pct, _ = data_row("rf_us", "U.S. 10Y yield", f"Trading Economics · {y_us['as_of']}",
                                    y_us["value"], 2, y_us["status"])
            rf_fc_pct, _ = data_row("rf_fc", f"{c_name} 10Y yield", f"Trading Economics · {y_fc['as_of']}",
                                    y_fc["value"], 2, y_fc["status"])
            tax_us_pct, _ = data_row("tax_us", "U.S. tax rate", f"OECD combined statutory · {tax_us_live[1]}",
                                     tax_us_live[0], 2, tax["status"])
            tax_fc_pct, _ = data_row("tax_fc", f"{c_name} tax rate", f"OECD combined statutory · {tax_fc_live[1]}",
                                     tax_fc_live[0], 2, tax["status"])
            spread_us_pct, _ = data_row("spread_us", "USD BBB spread", f"Damodaran Baa2/BBB · {bbb['as_of']}",
                                        bbb["value"], 2, bbb["status"])

            auto_spread_pct = 100 * auto_fc_spread(spread_us_pct / 100, rf_us_pct / 100, rf_fc_pct / 100, method)
            if method == "multiplicative":
                spread_rule = (f"{spread_us_pct:.2f}% × (1 + {rf_fc_pct:.2f}%) ÷ (1 + {rf_us_pct:.2f}%) "
                               f"= {auto_spread_pct:.4f}%")
            else:
                spread_rule = f"USD spread (additive method) = {auto_spread_pct:.4f}%"
            sp_val, sp_on = data_row("spread_fc", f"{fcl} BBB spread", "Derived from USD spread and yields",
                                     auto_spread_pct, 4, "Auto")
            spread_fc_override = sp_val / 100 if sp_on else None
            html_(f'<div class="coc-note">{"manual in use · auto would be " if sp_on else "auto = "}'
                  f'{_e(spread_rule)}</div>')

    project_tax_pct = tax_us_pct if tax_basis == "us" else tax_fc_pct
    tax_label = "U.S." if tax_basis == "us" else country

    res = compute(beta_u=beta_u, rf_us=rf_us_pct / 100, rf_fc=rf_fc_pct / 100, erp=erp_pct / 100,
                  debt_share=debt_pct / 100, tax=project_tax_pct / 100, spread_usd=spread_us_pct / 100,
                  spread_fc_override=spread_fc_override, method=method)

    # ---------- header status pill
    statuses = [betas_status, bbb["status"], tax["status"],
                "Live" if str(y_us["status"]).startswith("Live") and str(y_fc["status"]).startswith("Live") else "x"]
    n_live = sum(str(s).startswith("Live") for s in statuses)
    dot = "#4ADE80" if n_live == 4 else "#FFB35C"
    html_(f'<div style="display:flex;justify-content:flex-end"><span class="coc-pill"><span class="coc-dot" '
          f'style="background:{dot}"></span>{n_live} of 4 sources live · fetched {_e(bbb["fetched"])}</span></div>',
          pill_slot)

    # ======================= RIGHT: RESULTS =======================
    with right:
        chips = (f'<span class="coc-chip">Tax {res["tax"]:.2%} ({_e(tax_label)})</span>'
                 f'<span class="coc-chip">ERP {erp_pct:.1f}%</span><span class="coc-chip">Debt {debt_pct:.0f}%</span>'
                 f'<span class="coc-chip">{"Exact parity" if method == "multiplicative" else "Additive"}</span>')
        html_(f'<div class="coc-head"><div><div class="coc-eyebrow">Results</div>'
              f'<div class="coc-h2">{_e(industry)}</div></div><div class="coc-chips">{chips}</div></div>')

        def card(ccy_label, sub, color, wacc, ke, kd, kd_at, delta=None):
            d = f'<span class="delta">{_e(delta)}</span>' if delta else ""
            return (f'<div class="coc-card"><div class="bar" style="background:{color}"></div>'
                    f'<div class="top"><div><span class="sw" style="background:{color}"></span>'
                    f'<span class="ccy">{_e(ccy_label)}</span><span class="csub">{_e(sub)}</span></div>{d}</div>'
                    f'<div class="k">WACC</div><div class="big">{_pct(wacc)}</div>'
                    f'<div class="stats"><div><div>Cost of equity</div><div>{_pct(ke)}</div></div>'
                    f'<div><div>Pre-tax debt</div><div>{_pct(kd)}</div></div>'
                    f'<div><div>After-tax debt</div><div>{_pct(kd_at)}</div></div></div></div>')

        html_('<div class="coc-cards">'
              + card("USD", f"United States · 10Y {_pct(res['rf_us'])}", USD_COLOR, res["wacc_usd"], res["ke_usd"],
                     res["kd_usd"], res["kd_usd_at"])
              + card(fcl, f"{country} · 10Y {_pct(res['rf_fc'])}", FC_COLOR, res["wacc_fc"], res["ke_fc"],
                     res["kd_fc"], res["kd_fc_at"], f"{_pp(res['wacc_fc'] - res['wacc_usd'])} vs USD")
              + '</div>')

        dy, dw = res["rf_fc"] - res["rf_us"], res["wacc_fc"] - res["wacc_usd"]
        if abs(dy) < 1e-12 and not res["spread_fc_overridden"]:
            insight = (f"Both 10-year yields are {_pct(res['rf_us'])}, so the project costs the same in either "
                       f"currency: WACC {_pct(res['wacc_usd'])}.")
        elif abs(dy) < 1e-12:
            insight = (f"Both 10-year yields are {_pct(res['rf_us'])}; the only difference is the overridden local "
                       f"spread, so {fcl} WACC is {_pct(res['wacc_fc'])} vs {_pct(res['wacc_usd'])} in USD.")
        else:
            insight = (f"In {fcl}, WACC is {abs(dw) * 100:.2f} pp {'lower' if dw < 0 else 'higher'} than in USD "
                       f"({_pct(res['wacc_fc'])} vs {_pct(res['wacc_usd'])}), because {country}’s 10-year yield is "
                       f"{abs(dy) * 100:.2f} pp {'below' if dy < 0 else 'above'} the U.S. yield"
                       + (" and the local spread is overridden." if res["spread_fc_overridden"] else "."))
        html_(f'<div class="coc-insight">{INFO_SVG}<div>{_e(insight)}</div></div>')

        # ---- data for downloads / tables
        export = pd.DataFrame([{
            "Industry": industry, "Country": country, "Currency": ccy, "Conversion method": method,
            "Tax basis": tax_label, "Project tax rate": res["tax"], "ERP": res["erp"], "Debt share": res["w_d"],
            "Unlevered beta": res["beta_u"], "Levered beta": res["beta_l"],
            "US 10Y": res["rf_us"], f"{fcl} 10Y": res["rf_fc"],
            "USD BBB spread": res["spread_usd"], f"{fcl} BBB spread": res["spread_fc"],
            f"{fcl} spread overridden": res["spread_fc_overridden"],
            "Cost of equity USD": res["ke_usd"], f"Cost of equity {fcl}": res["ke_fc"],
            "Pre-tax cost of debt USD": res["kd_usd"], f"Pre-tax cost of debt {fcl}": res["kd_fc"],
            "After-tax cost of debt USD": res["kd_usd_at"], f"After-tax cost of debt {fcl}": res["kd_fc_at"],
            "WACC USD": res["wacc_usd"], f"WACC {fcl}": res["wacc_fc"], "Generated": _now(),
        }]).T.rename(columns={0: "Value"})

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

        _, d1, d2 = st.columns([2.2, 1, 1.25], vertical_alignment="center")
        d1.download_button("Results CSV", export.to_csv().encode(), file_name=f"cost_of_capital_{slug_fc}.csv",
                           mime="text/csv", icon=":material/download:", use_container_width=True)
        d2.download_button("All industries CSV", all_df.to_csv(index=False).encode(),
                           file_name=f"all_industries_{slug_fc}.csv", mime="text/csv",
                           icon=":material/download:", use_container_width=True)

        tab_b, tab_i, tab_c, tab_m = st.tabs(["Breakdown", "All industries", "Checks", "Method & sources"])

        # ---- Breakdown
        with tab_b:
            f4 = lambda x: _pct(x, 4)  # noqa: E731
            brows = [
                ("Risk-free rate (10Y govt.)", f4(res["rf_us"]), f4(res["rf_fc"]), False),
                ("Unlevered beta", f"{res['beta_u']:.4f}", f"{res['beta_u']:.4f}", False),
                ("Levered beta", f"{res['beta_l']:.4f}", f"{res['beta_l']:.4f}", False),
                ("Equity risk premium", f4(res["erp"]), f4(res["erp"]), False),
                ("Cost of equity", f4(res["ke_usd"]), f4(res["ke_fc"]), True),
                ("BBB default spread", f4(res["spread_usd"]),
                 f4(res["spread_fc"]) + (" (manual)" if res["spread_fc_overridden"] else " (auto)"), False),
                ("Pre-tax cost of debt", f4(res["kd_usd"]), f4(res["kd_fc"]), False),
                ("Project tax rate", f4(res["tax"]), f4(res["tax"]), False),
                ("After-tax cost of debt", f4(res["kd_usd_at"]), f4(res["kd_fc_at"]), False),
                ("Equity weight E/V", _pct(res["w_e"]), _pct(res["w_e"]), False),
                ("Debt weight D/V", _pct(res["w_d"]), _pct(res["w_d"]), False),
                ("WACC", f4(res["wacc_usd"]), f4(res["wacc_fc"]), True),
            ]
            body = "".join(f'<tr class="{"strong" if s else ""}"><td>{_e(a)}</td><td>{_e(b)}</td><td>{_e(c)}</td></tr>'
                           for a, b, c, s in brows)
            html_(f'<div style="overflow-x:auto"><table class="coc-table"><thead><tr><th>Component</th>'
                  f'<th style="color:{USD_COLOR}">USD</th><th style="color:{FC_COLOR}">{_e(fcl)}</th></tr></thead>'
                  f'<tbody>{body}</tbody></table></div>')
            mode = ("Manual override", "coc-b-man") if res["spread_fc_overridden"] else ("Auto", "coc-b-auto")
            html_(f'<div class="coc-box"><h4>How the {_e(fcl)} BBB spread is set '
                  f'<span class="coc-badge {mode[1]}">{mode[0]}</span></h4>'
                  f'The USD cost of debt is translated with the same parity factor used for equity, then the local '
                  f'risk-free rate is subtracted, which scales the USD spread by (1 + r_FC) ÷ (1 + r_US). Debt and equity '
                  f'are converted consistently, and equal yields give back the USD spread exactly. Override it in '
                  f'Market data to use a quoted local spread; USD results never change.'
                  f'<div class="coc-mono" style="margin-top:10px">auto = {_e(spread_rule)} · in use: '
                  f'{res["spread_fc"]:.4%}</div></div>')

        # ---- All industries
        with tab_i:
            q = st.text_input("Filter industries", key="ind_q", placeholder="Filter industries",
                              label_visibility="collapsed")
            view = all_df[all_df["Industry"].str.contains(q, case=False, regex=False)] if q else all_df
            mx = max(float(all_df[["WACC USD", f"WACC {fcl}"]].max().max()), 1e-4)
            html_(f'<div class="coc-legend"><span><i style="background:{USD_COLOR}"></i>WACC USD</span>'
                  f'<span><i style="background:{FC_COLOR}"></i>WACC {_e(fcl)}</span>'
                  f'<span style="color:#8A93A5">{len(view)} of {len(all_df)} industries · sorted by USD WACC · '
                  f'selected industry highlighted</span></div>')
            items = []
            for _, r in view.iterrows():
                wu, wf = r["WACC USD"], r[f"WACC {fcl}"]
                firms = "" if pd.isna(r["Firms"]) else f"{int(r['Firms'])} firms · "
                items.append(
                    f'<div class="coc-irow{" sel" if r["Industry"] == industry else ""}">'
                    f'<div class="n"><span>{_e(r["Industry"])}</span><small>{firms}βu {r["Unlevered beta"]:.2f} · '
                    f'βL {r["Levered beta"]:.2f}</small></div>'
                    f'<div class="bars"><i style="width:{max(wu, 0) / mx * 100:.1f}%;background:{USD_COLOR}"></i>'
                    f'<i style="width:{max(wf, 0) / mx * 100:.1f}%;background:{FC_COLOR}"></i></div>'
                    f'<div class="v"><span>{_pct(wu)}</span><span>{_pct(wf)}</span></div></div>')
            html_(f'<div class="coc-ind">{"".join(items)}</div>')

        # ---- Checks
        with tab_c:
            base = dict(beta_u=beta_u, erp=res["erp"], debt_share=res["w_d"], tax=res["tax"],
                        spread_usd=res["spread_usd"], method=method)
            eq = compute(rf_us=res["rf_us"], rf_fc=res["rf_us"], spread_fc_override=None, **base)
            c1 = abs(eq["ke_usd"] - eq["ke_fc"]) < 1e-12 and abs(eq["wacc_usd"] - eq["wacc_fc"]) < 1e-12
            z = compute(rf_us=res["rf_us"], rf_fc=res["rf_fc"], spread_fc_override=spread_fc_override,
                        **{**base, "debt_share": 0.0})
            c2 = abs(z["wacc_usd"] - z["ke_usd"]) < 1e-12 and abs(z["wacc_fc"] - z["ke_fc"]) < 1e-12
            a = compute(rf_us=res["rf_us"], rf_fc=res["rf_fc"], spread_fc_override=None, **base)
            b = compute(rf_us=res["rf_us"], rf_fc=res["rf_fc"], spread_fc_override=a["spread_fc_auto"] + 0.02, **base)
            c3 = all(a[k] == b[k] for k in ["ke_usd", "kd_usd", "wacc_usd"]) and b["kd_fc"] != a["kd_fc"]
            checks = [
                (c1, "Equal 10Y yields give equal results (spread override off)",
                 f"Cost of equity {eq['ke_usd']:.4%} vs {eq['ke_fc']:.4%} · WACC {eq['wacc_usd']:.4%} vs "
                 f"{eq['wacc_fc']:.4%}"),
                (c2, "Zero debt makes WACC equal the cost of equity",
                 f"USD {z['wacc_usd']:.4%} = {z['ke_usd']:.4%} · {fcl} {z['wacc_fc']:.4%} = {z['ke_fc']:.4%}"),
                (c3, f"Overriding the {fcl} spread (+2 pp) leaves USD results unchanged",
                 f"USD WACC {a['wacc_usd']:.4%} → {b['wacc_usd']:.4%} · {fcl} debt {a['kd_fc']:.4%} → "
                 f"{b['kd_fc']:.4%}, WACC {a['wacc_fc']:.4%} → {b['wacc_fc']:.4%}"),
            ]
            html_("".join(
                f'<div class="coc-check"><span class="coc-badge {"coc-b-live" if ok else "coc-b-snap"}" '
                f'style="margin:2px 0 0;font-size:12px;padding:4px 10px">{"✓ Pass" if ok else "✗ Fail"}</span>'
                f'<div><div class="name">{_e(n)}</div><div class="det">{_e(d)}</div></div></div>'
                for ok, n, d in checks))
            ke_fc_line = (
                f"(1 + {res['ke_usd']:.6f}) x (1 + {res['rf_fc']:.6f}) / (1 + {res['rf_us']:.6f}) - 1"
                if method == "multiplicative"
                else f"{res['ke_usd']:.6f} + ({res['rf_fc']:.6f} - {res['rf_us']:.6f})"
            )
            st.markdown("**Step by step, for checking by hand**")
            st.code(
                f"""D/E            = {res['w_d']:.4f} / {res['w_e']:.4f} = {res['de_ratio']:.6f}
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

        # ---- Method & sources
        with tab_m:
            m1, m2 = st.columns(2, gap="large")
            with m1:
                st.markdown("**Formulas**")
                st.latex(r"\frac{D}{E} = \frac{w_d}{1-w_d},\quad \beta_L = \beta_U\left[1 + (1-t)\frac{D}{E}\right]")
                st.latex(r"k_e^{USD} = r_{US} + \beta_L \cdot ERP,\quad k_d^{USD} = r_{US} + s_{BBB}")
                st.latex(r"WACC = (1-w_d)\,k_e + w_d\,k_d\,(1-t)")
                st.latex(r"k_e^{FC} = (1+k_e^{USD})\frac{1+r_{FC}}{1+r_{US}} - 1")
                st.latex(r"s^{FC}_{auto} = s_{BBB}\,\frac{1+r_{FC}}{1+r_{US}},\quad k_d^{FC} = r_{FC} + s^{FC}")
                st.caption("Additive option: ke FC = ke USD + (r FC − r US) and s FC = s BBB.")
                st.markdown("**Main assumptions**")
                html_('<ol class="coc-ol">'
                      '<li>The 10-year local government yield is each currency’s risk-free rate.</li>'
                      '<li>The U.S. industry unlevered beta is the project’s business risk in both currencies, '
                      'relevered at the target debt share (Hamada).</li>'
                      '<li>The same equity risk premium applies in both currencies; no separate country risk premium.</li>'
                      '<li>Cost of debt = risk-free rate + BBB (Baa2) spread for large non-financial firms.</li>'
                      '<li>The yield gap is the expected inflation / depreciation gap (international Fisher effect).</li>'
                      '<li>One project tax rate (U.S. or local) is used everywhere, so the two columns differ only by '
                      'currency translation.</li>'
                      '<li>Tax = OECD combined statutory corporate rate, latest year; weights are target market values.</li>'
                      '</ol>')
            with m2:
                st.markdown("**Sources and dates**")
                tax_fc_year = tax["rates"].get(iso_fc, (None, "n/a"))[1]
                srcs = [
                    (f"Unlevered beta — {industry}", f"{beta_u_live:.4f}", f"Updated {betas['as_of']}",
                     betas_status, "Damodaran betas.xls", BETAS_URL),
                    ("BBB (Baa2) spread, large non-financial firms", f"{bbb['value']:.2f}%", bbb["as_of"],
                     bbb["status"], "Damodaran ratings", bbb["url"]),
                    ("Corporate tax — United States", f"{tax_us_live[0]:.2f}%", f"Tax year {tax_us_live[1]}",
                     tax["status"], "OECD Corporate Tax Statistics", OECD_PAGE_URL),
                    (f"Corporate tax — {country}", f"{tax_fc_live[0]:.2f}%", f"Tax year {tax_fc_year}",
                     tax["status"], "OECD Corporate Tax Statistics", OECD_PAGE_URL),
                    ("10Y yield — United States", f"{y_us['value']:.2f}%", y_us["as_of"], y_us["status"],
                     "Trading Economics", y_us["url"]),
                    (f"10Y yield — {country}", f"{y_fc['value']:.2f}%", y_fc["as_of"], y_fc["status"],
                     "Trading Economics", y_fc["url"]),
                ]
                html_('<div class="coc-srcs">' + "".join(
                    f'<div class="coc-src-row"><div class="t"><span>{_e(i)}</span><b>{_e(v)}</b></div>'
                    f'<div class="d">{_e(d)} · {_e(stt)} · <a href="{_e(u)}" target="_blank" rel="noopener">'
                    f'{_e(site)}</a></div></div>' for i, v, d, stt, site, u in srcs) + '</div>')
                st.caption(f"Fetched {bbb['fetched']}. OECD figures are read through the official OECD Data "
                           "Explorer API (same dataset as the linked publication). Trading Economics yields come "
                           "from each country's government-bond page when the /bonds overview table is not served. "
                           "A row marked Snapshot means the live fetch failed and a dated 2026-11-16 snapshot is used.")

        html_('<div class="coc-foot">Defaults on open: Software (System &amp; Application) · United States · '
              'U.S. tax rate · 6.5% ERP · 30% debt.</div>')


if __name__ == "__main__":
    run_app()
