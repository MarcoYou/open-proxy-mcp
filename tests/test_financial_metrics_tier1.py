import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services.financial_metrics import _compute_metrics


def _base_bs_is():
    return {
        "current_assets": 500,
        "non_current_assets": 500,
        "total_assets": 1_000,
        "current_liabilities": 200,
        "non_current_liabilities": 200,
        "total_liabilities": 400,
        "capital_stock": 100,
        "retained_earnings": 300,
        "total_equity": 600,
        "revenue": 1_000,
        "operating_profit": 100,
        "income_before_tax": 90,
        "net_income": 80,
        "comprehensive_income": 80,
    }


def test_tier1_working_capital_days_and_cfo_to_net_income_ratio():
    metrics = _compute_metrics(
        bs_is=_base_bs_is(),
        bs_is_prev={**_base_bs_is(), "total_assets": 900, "total_equity": 500},
        detail={
            "gross_profit": 400,
            "cogs": 600,
            "cfo": 120,
            "capex": -20,
            "accounts_receivable": 120,
            "inventory": 90,
            "accounts_payable": 50,
        },
        detail_prev={
            "accounts_receivable": 80,
            "inventory": 60,
            "accounts_payable": 30,
        },
        indx_map=None,
    )

    assert metrics["cfo_to_net_income_ratio"] == 1.5
    assert metrics["days_sales_outstanding"] == 36.5
    assert metrics["days_inventory_outstanding"] == 45.6
    assert metrics["days_payable_outstanding"] == 24.3
    assert metrics["cash_conversion_cycle_days"] == 57.8


def test_tier1_days_are_none_when_denominator_is_missing_or_non_positive():
    metrics = _compute_metrics(
        bs_is={**_base_bs_is(), "revenue": 0},
        bs_is_prev=None,
        detail={
            "cfo": 120,
            "accounts_receivable": 120,
            "inventory": 90,
            "accounts_payable": 50,
        },
        detail_prev=None,
        indx_map=None,
    )

    assert metrics["days_sales_outstanding"] is None
    assert metrics["days_inventory_outstanding"] is None
    assert metrics["days_payable_outstanding"] is None
    assert metrics["cash_conversion_cycle_days"] is None
