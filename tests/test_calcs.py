"""Unit tests for the cost-of-capital math (no network). Run: pytest -q"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import auto_fc_spread, compute, convert_rate, levered_beta  # noqa: E402

BASE = dict(beta_u=1.225392, erp=0.065, debt_share=0.30, tax=0.255285, spread_usd=0.0111)


def test_hand_example():
    """Hand-calculated: beta_U 1.2, w_d 30%, t 25%, ERP 6.5%, rf_US 4%, rf_FC 6%, spread 1%."""
    r = compute(beta_u=1.2, rf_us=0.04, rf_fc=0.06, erp=0.065, debt_share=0.30, tax=0.25, spread_usd=0.01)
    de = 0.3 / 0.7
    bl = 1.2 * (1 + 0.75 * de)
    assert r["beta_l"] == pytest.approx(bl)                     # 1.585714
    ke = 0.04 + bl * 0.065
    assert r["ke_usd"] == pytest.approx(ke)                     # 0.143071
    assert r["wacc_usd"] == pytest.approx(0.7 * ke + 0.3 * 0.05 * 0.75)
    ke_fc = (1 + ke) * 1.06 / 1.04 - 1
    assert r["ke_fc"] == pytest.approx(ke_fc)
    s_fc = 0.01 * 1.06 / 1.04
    assert r["spread_fc"] == pytest.approx(s_fc)
    assert r["wacc_fc"] == pytest.approx(0.7 * ke_fc + 0.3 * (0.06 + s_fc) * 0.75)


@pytest.mark.parametrize("method", ["multiplicative", "additive"])
def test_equal_yields_equal_results(method):
    r = compute(rf_us=0.05, rf_fc=0.05, method=method, **BASE)
    for a, b in [("ke_usd", "ke_fc"), ("kd_usd", "kd_fc"), ("wacc_usd", "wacc_fc"), ("spread_usd", "spread_fc")]:
        assert r[a] == pytest.approx(r[b], abs=1e-15)


@pytest.mark.parametrize("method", ["multiplicative", "additive"])
def test_zero_debt_wacc_equals_ke(method):
    r = compute(rf_us=0.05, rf_fc=0.09, method=method, **{**BASE, "debt_share": 0.0})
    assert r["wacc_usd"] == pytest.approx(r["ke_usd"])
    assert r["wacc_fc"] == pytest.approx(r["ke_fc"])
    assert r["beta_l"] == pytest.approx(BASE["beta_u"])


def test_fc_spread_override_leaves_usd_unchanged():
    a = compute(rf_us=0.05, rf_fc=0.09, **BASE)
    b = compute(rf_us=0.05, rf_fc=0.09, spread_fc_override=0.04, **BASE)
    for k in ["beta_l", "ke_usd", "kd_usd", "kd_usd_at", "wacc_usd", "ke_fc"]:
        assert a[k] == b[k]
    assert b["kd_fc"] == pytest.approx(0.09 + 0.04)
    assert b["wacc_fc"] != a["wacc_fc"]


def test_conversion_helpers():
    assert convert_rate(0.10, 0.04, 0.04) == pytest.approx(0.10)
    assert convert_rate(0.10, 0.04, 0.08, "additive") == pytest.approx(0.14)
    assert auto_fc_spread(0.0111, 0.05, 0.05) == pytest.approx(0.0111)
    assert levered_beta(1.0, 0.5, 0.0) == pytest.approx(2.0)
