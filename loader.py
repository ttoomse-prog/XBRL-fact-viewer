"""
Shared loader: takes zip bytes, runs Arelle, returns a DataFrame of facts.
Cached at session level so all pages share the same loaded data.
"""
import os
import tempfile
import zipfile
import pandas as pd
import streamlit as st


# ── ESEF statement classification ─────────────────────────────────────────────
# Maps common IFRS namespace concept prefixes / names to a statement bucket.
# Extend as needed.

STATEMENT_MAP = {
    # Income statement / P&L
    "Revenue": "Income Statement",
    "GrossProfit": "Income Statement",
    "OperatingIncomeLoss": "Income Statement",
    "ProfitLoss": "Income Statement",
    "ProfitLossFromOperatingActivities": "Income Statement",
    "ProfitLossBeforeTax": "Income Statement",
    "IncomeTaxExpenseContinuingOperations": "Income Statement",
    "ProfitLossFromContinuingOperations": "Income Statement",
    "ProfitLossAttributableToOwnersOfParent": "Income Statement",
    "ProfitLossAttributableToNoncontrollingInterests": "Income Statement",
    "BasicEarningsLossPerShare": "Income Statement",
    "DilutedEarningsLossPerShare": "Income Statement",
    "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss": "Income Statement",
    "FinanceCosts": "Income Statement",
    "FinanceIncome": "Income Statement",
    "OtherIncome": "Income Statement",
    "OtherExpenseByFunction": "Income Statement",
    "DistributionCosts": "Income Statement",
    "AdministrativeExpense": "Income Statement",
    "CostOfSales": "Income Statement",
    # Balance sheet
    "Assets": "Balance Sheet",
    "NoncurrentAssets": "Balance Sheet",
    "CurrentAssets": "Balance Sheet",
    "Liabilities": "Balance Sheet",
    "NoncurrentLiabilities": "Balance Sheet",
    "CurrentLiabilities": "Balance Sheet",
    "Equity": "Balance Sheet",
    "EquityAttributableToOwnersOfParent": "Balance Sheet",
    "NoncontrollingInterests": "Balance Sheet",
    "CashAndCashEquivalents": "Balance Sheet",
    "TradeAndOtherCurrentReceivables": "Balance Sheet",
    "Inventories": "Balance Sheet",
    "PropertyPlantAndEquipment": "Balance Sheet",
    "IntangibleAssetsOtherThanGoodwill": "Balance Sheet",
    "Goodwill": "Balance Sheet",
    "TradeAndOtherCurrentPayables": "Balance Sheet",
    "NoncurrentPortionOfNoncurrentBorrowings": "Balance Sheet",
    "CurrentPortionOfNoncurrentBorrowings": "Balance Sheet",
    "IssuedCapital": "Balance Sheet",
    "RetainedEarnings": "Balance Sheet",
    # Cash flow
    "CashFlowsFromUsedInOperatingActivities": "Cash Flow",
    "CashFlowsFromUsedInInvestingActivities": "Cash Flow",
    "CashFlowsFromUsedInFinancingActivities": "Cash Flow",
    "IncreaseDecreaseInCashAndCashEquivalents": "Cash Flow",
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": "Cash Flow",
    "ProceedsFromIssuingShares": "Cash Flow",
    "DividendsPaidClassifiedAsFinancingActivities": "Cash Flow",
    "AdjustmentsForDepreciationAndAmortisationExpense": "Cash Flow",
    # OCI
    "OtherComprehensiveIncome": "Other Comprehensive Income",
    "ComprehensiveIncome": "Other Comprehensive Income",
}


def classify_statement(concept_local_name: str) -> str:
    """Return statement bucket for a concept, or 'Other / Extension'."""
    if concept_local_name in STATEMENT_MAP:
        return STATEMENT_MAP[concept_local_name]
    # Heuristic keyword matching
    name_lower = concept_local_name.lower()
    if any(k in name_lower for k in ["revenue", "profit", "loss", "income", "expense", "earnings", "ebit", "ebitda", "sales", "cost"]):
        return "Income Statement"
    if any(k in name_lower for k in ["asset", "liabilit", "equity", "cash", "inventor", "payable", "receivable", "goodwill", "capital", "reserve", "borrowing"]):
        return "Balance Sheet"
    if any(k in name_lower for k in ["cashflow", "cash flow", "operating activit", "investing activit", "financing activit"]):
        return "Cash Flow"
    return "Other / Extension"


def find_entry_point(extract_dir: str) -> str | None:
    """Find the iXBRL / XBRL entry point inside an ESEF package."""
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            if f.endswith(('.xhtml', '.htm', '.html')) and 'report' in root.lower():
                return os.path.join(root, f)
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            if f.endswith(('.xhtml', '.htm', '.html')):
                return os.path.join(root, f)
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            if f.endswith('.xml') and not f.startswith('.'):
                return os.path.join(root, f)
    return None


@st.cache_data(show_spinner=False)
def load_facts(zip_bytes: bytes) -> tuple[pd.DataFrame, list[str], dict]:
    """
    Load an ESEF zip via Arelle and return (facts_df, log_messages, metadata).
    Cached so all pages share the result without re-processing.
    """
    from arelle import Cntlr, ModelManager

    logs = []
    meta = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "report.zip")
        with open(zip_path, "wb") as f:
            f.write(zip_bytes)

        extract_dir = os.path.join(tmpdir, "extracted")
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(extract_dir)

        entry = find_entry_point(extract_dir)
        if not entry:
            raise FileNotFoundError("Could not find an XBRL/iXBRL entry point in the zip.")

        logs.append(f"Entry point: {os.path.relpath(entry, extract_dir)}")

        cntlr = Cntlr.Cntlr(logFileName="logToStdErr")
        model_manager = ModelManager.initialize(cntlr)
        model_xbrl = model_manager.load(entry)

        if model_xbrl is None:
            raise RuntimeError("Arelle failed to load the report.")

        rows = []
        for fact in model_xbrl.facts:
            concept = fact.concept
            if concept is None:
                continue

            try:
                label = concept.label(lang="en") or concept.qname.localName
            except Exception:
                label = concept.qname.localName

            ctx = fact.context
            if ctx is None:
                period_type = period_start = period_end = ""
            elif ctx.isInstantPeriod:
                period_type = "instant"
                period_start = ""
                period_end = str(ctx.instantDatetime.date()) if ctx.instantDatetime else ""
            elif ctx.isStartEndPeriod:
                period_type = "duration"
                period_start = str(ctx.startDatetime.date()) if ctx.startDatetime else ""
                period_end = str(ctx.endDatetime.date()) if ctx.endDatetime else ""
            else:
                period_type = "forever"
                period_start = period_end = ""

            unit = fact.unit
            unit_str = ""
            if unit is not None:
                try:
                    unit_str = str(unit.value)
                except Exception:
                    unit_str = str(unit)

            dims = {}
            if ctx is not None:
                for dim_qname, dim_val in ctx.qnameDims.items():
                    dim_name = dim_qname.localName
                    try:
                        dims[dim_name] = dim_val.memberQname.localName if dim_val.isExplicit else str(dim_val.typedMember)
                    except Exception:
                        dims[dim_name] = str(dim_val)

            try:
                value = fact.value
            except (KeyError, AttributeError, Exception):
                try:
                    value = fact.text
                except Exception:
                    value = ""

            local_name = concept.qname.localName
            ns = concept.qname.namespaceURI or ""

            rows.append({
                "Concept": local_name,
                "Label": label,
                "Namespace": ns,
                "Statement": classify_statement(local_name),
                "Period Type": period_type,
                "Period Start": period_start,
                "Period End": period_end,
                "Value": value,
                "Unit": unit_str,
                "Decimals": fact.decimals or "",
                "Entity": ctx.entityIdentifier[1] if ctx and ctx.entityIdentifier else "",
                "Dimensions": "; ".join(f"{k}={v}" for k, v in dims.items()) if dims else "",
            })

        # Grab entity name if available
        try:
            for ctx in model_xbrl.contexts.values():
                if ctx.entityIdentifier:
                    meta["entity_id"] = ctx.entityIdentifier[1]
                    break
        except Exception:
            pass

        logs.append(f"Facts found: {len(rows)}")
        model_manager.close()

    COLUMNS = ["Concept", "Label", "Namespace", "Statement", "Period Type",
               "Period Start", "Period End", "Value", "Unit", "Decimals",
               "Entity", "Dimensions"]

    if not rows:
        df = pd.DataFrame(columns=COLUMNS)
        df["_numeric"] = pd.Series(dtype=float)
        return df, logs, meta

    df = pd.DataFrame(rows, columns=COLUMNS)

    df["Value"] = pd.to_numeric(df["Value"], errors="ignore")
    df["_numeric"] = pd.to_numeric(df["Value"], errors="coerce")

    return df, logs, meta
