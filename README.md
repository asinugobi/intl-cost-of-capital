# International Cost of Capital App

A Streamlit app that compares a project's **cost of equity, cost of debt and WACC in USD** and in the
**currency of a selected country**, using live data from Damodaran, the OECD and Trading Economics.

- **Live app:** https://intl-cost-of-capital-proto.streamlit.app/
- **Repository:** https://github.com/asinugobi/intl-cost-of-capital
- **Submission document:** https://claude.ai/artifact/CyYQNXPqaNRuAjsgEjQmYE
- **UI design prototype (interactive):** https://claude.ai/artifact/5Lk4oH7jTjbpFfmh8ffXUK

## Features

- **Live data with sources and dates** (cached, with a **Refresh data fetch** button):
  | Input | Source | What is used |
  |---|---|---|
  | Industry unlevered beta | [Damodaran `betas.xls`](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls) | Sheet *Industry Averages*, column **Unlevered beta** (U.S. firms) |
  | BBB borrowing spread (USD) | [Damodaran `ratings.html`](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html) | **Baa2/BBB**, large non-financial service firms |
  | Corporate tax rates | [OECD Corporate Tax Statistics](https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html) | Combined (central + sub-central) statutory CIT rate, latest year, read via the official [OECD Data Explorer API](https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all?format=csvfilewithlabels) |
  | 10-year government yields | [Trading Economics](https://tradingeconomics.com/bonds) | 10Y yield; if the `/bonds` overview table is not served, the country page `/<country>/government-bond-yield` is read instead |
- **Choices:** industry (all ~94 Damodaran industries), target country (50 countries), equity risk premium, target debt share,
  project tax rate switch (**U.S.** or **selected country**), and currency-conversion method (multiplicative parity or
  additive approximation).
- **Manual overrides** (toggle on/off) for unlevered beta, U.S. and foreign tax rates, USD BBB spread, U.S. and foreign
  10Y yields, and the **foreign-currency BBB spread** (when off, the auto-calculated value is shown).
- **Side-by-side results** in USD and foreign currency, a full breakdown table, an **all-industry comparison** with chart,
  and **CSV downloads**.
- **Method & assumptions** tab and a **Verification checks** tab that tests the three required properties live, plus a
  step-by-step calculation printout for checking by hand.
- **Dark, low-effort layout:** inputs in three numbered steps (Project → Assumptions → Market data, where every fetched value shows its source, date and a Live/Manual/Auto badge next to its override switch), big side-by-side USD and local-currency result cards with a one-line summary, and detail tabs.
- **Defaults on open:** Software (System & Application), United States, U.S. tax rate, 6.5% ERP, 30% debt.

## Formulas

With debt share *w_d*, project tax rate *t*, U.S. yield *r_US*, foreign yield *r_FC*, ERP and USD BBB spread *s*:

```
D/E        = w_d / (1 − w_d)
β_L        = β_U × [1 + (1 − t) × D/E]                          (Hamada)

USD:  k_e  = r_US + β_L × ERP
      k_d  = r_US + s
      WACC = (1 − w_d) k_e + w_d k_d (1 − t)

FC:   k_e  = (1 + k_e,USD) × (1 + r_FC)/(1 + r_US) − 1           (international Fisher / parity)
      s_FC = s × (1 + r_FC)/(1 + r_US)   [auto; can be overridden]
      k_d  = r_FC + s_FC
      WACC = (1 − w_d) k_e,FC + w_d k_d,FC (1 − t)
```
The additive option uses `k_e,FC = k_e,USD + (r_FC − r_US)` and `s_FC = s`.

## Main assumptions

1. The 10Y local-currency government yield is the risk-free rate in each currency.
2. U.S. industry unlevered beta is the project's business risk in both currencies, relevered at the target debt share.
3. Same ERP in both currencies; no separate country risk premium.
4. The yield differential captures expected inflation / currency depreciation (international Fisher effect).
5. One project tax rate (U.S. or selected country) is used everywhere, so the two columns differ only by currency translation.

## Verification

- `tests/test_calcs.py` (run `pytest -q`) checks a hand-calculated example and the three required properties:
  equal yields → equal results (spread override off); zero debt → WACC = cost of equity; overriding the foreign spread
  leaves USD results unchanged.
- `verification_example.xlsx` re-computes the Germany example with plain Excel formulas for an independent check.

Worked example (defaults, live data of 2026-11-16, Germany selected): β_U = 1.2254, t = 25.53%, r_US = 5.14%, r_DE = 3.38%,
s = 1.11% → β_L = 1.6165, k_e USD = 15.65%, WACC USD = 12.35%, k_e EUR = 13.71%, WACC EUR = 10.60%.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Push this repository to GitHub (`app.py`, `requirements.txt`, `README.md` at the root).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** → pick the repo, branch `main`, main file `app.py` → **Deploy**.

If a live source is unreachable from the server, the app falls back to a dated snapshot and labels it
*Fallback snapshot* in the Sources tab; manual overrides are always available.
