"""
Shared loader: takes zip bytes, runs Arelle, returns a DataFrame of facts.
skipDTS=True avoids network calls to ESMA taxonomy servers.
"""
import os
import tempfile
import zipfile
import pandas as pd
import streamlit as st

STATEMENT_MAP = {
    "Revenue": "Income Statement", "GrossProfit": "Income Statement",
    "OperatingIncomeLoss": "Income Statement", "ProfitLoss": "Income Statement",
    "ProfitLossFromOperatingActivities": "Income Statement", "ProfitLossBeforeTax": "Income Statement",
    "IncomeTaxExpenseContinuingOperations": "Income Statement", "ProfitLossFromContinuingOperations": "Income Statement",
    "ProfitLossAttributableToOwnersOfParent": "Income Statement", "BasicEarningsLossPerShare": "Income Statement",
    "DilutedEarningsLossPerShare": "Income Statement", "FinanceCosts": "Income Statement",
    "FinanceIncome": "Income Statement", "DistributionCosts": "Income Statement",
    "AdministrativeExpense": "Income Statement", "CostOfSales": "Income Statement",
    "Assets": "Balance Sheet", "NoncurrentAssets": "Balance Sheet", "CurrentAssets": "Balance Sheet",
    "Liabilities": "Balance Sheet", "NoncurrentLiabilities": "Balance Sheet", "CurrentLiabilities": "Balance Sheet",
    "Equity": "Balance Sheet", "EquityAttributableToOwnersOfParent": "Balance Sheet",
    "CashAndCashEquivalents": "Balance Sheet", "TradeAndOtherCurrentReceivables": "Balance Sheet",
    "Inventories": "Balance Sheet", "PropertyPlantAndEquipment": "Balance Sheet",
    "IntangibleAssetsOtherThanGoodwill": "Balance Sheet", "Goodwill": "Balance Sheet",
    "TradeAndOtherCurrentPayables": "Balance Sheet", "IssuedCapital": "Balance Sheet",
    "RetainedEarnings": "Balance Sheet",
    "CashFlowsFromUsedInOperatingActivities": "Cash Flow", "CashFlowsFromUsedInInvestingActivities": "Cash Flow",
    "CashFlowsFromUsedInFinancingActivities": "Cash Flow", "IncreaseDecreaseInCashAndCashEquivalents": "Cash Flow",
    "AdjustmentsForDepreciationAndAmortisationExpense": "Cash Flow",
    "OtherComprehensiveIncome": "Other Comprehensive Income", "ComprehensiveIncome": "Other Comprehensive Income",
}

def classify_statement(name):
    if name in STATEMENT_MAP:
        return STATEMENT_MAP[name]
    n = name.lower()
    if any(k in n for k in ["revenue","profit","loss","income","expense","earnings","ebit","sales","cost"]):
        return "Income Statement"
    if any(k in n for k in ["asset","liabilit","equity","cash","inventor","payable","receivable","goodwill","capital","reserve","borrowing"]):
        return "Balance Sheet"
    if any(k in n for k in ["cashflow","cash flow","operating activit","investing activit","financing activit"]):
        return "Cash Flow"
    return "Other / Extension"

def find_entry_point(extract_dir):
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            if f.endswith(('.xhtml','.htm','.html')) and 'report' in root.lower():
                return os.path.join(root, f)
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            if f.endswith(('.xhtml','.htm','.html')):
                return os.path.join(root, f)
    for root, dirs, files in os.walk(extract_dir):
        for f in files:
            if f.endswith('.xml') and not f.startswith('.'):
                return os.path.join(root, f)
    return None

@st.cache_data(show_spinner=False)
def load_facts(zip_bytes):
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
        model_manager.skipDTS = True  # avoids ESMA taxonomy network calls
        model_xbrl = model_manager.load(entry)

        if model_xbrl is None:
            raise RuntimeError("Arelle failed to load the report.")

        rows = []
        for fact in model_xbrl.facts:
            try:
                concept = fact.concept
                if concept is not None:
                    local_name = concept.qname.localName
                    ns = concept.qname.namespaceURI or ""
                    try:
                        label = concept.label(lang="en") or local_name
                    except Exception:
                        label = local_name
                else:
                    local_name = fact.qname.localName
                    ns = fact.qname.namespaceURI or ""
                    label = local_name
            except Exception:
                try:
                    local_name = fact.qname.localName
                    ns = ""
                    label = local_name
                except Exception:
                    continue

            try:
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
            except Exception:
                ctx = None
                period_type = period_start = period_end = ""

            unit_str = ""
            try:
                unit = fact.unit
                if unit is not None:
                    unit_str = str(unit.value)
            except Exception:
                pass

            dims = {}
            try:
                if ctx is not None:
                    for dq, dv in ctx.qnameDims.items():
                        try:
                            dims[dq.localName] = dv.memberQname.localName if dv.isExplicit else str(dv.typedMember)
                        except Exception:
                            dims[dq.localName] = str(dv)
            except Exception:
                pass

            value = ""
            try:
                value = fact.value
            except Exception:
                try:
                    value = fact.text or ""
                except Exception:
                    pass

            entity = ""
            try:
                if ctx and ctx.entityIdentifier:
                    entity = ctx.entityIdentifier[1]
            except Exception:
                pass

            rows.append({
                "Concept": local_name, "Label": label, "Namespace": ns,
                "Statement": classify_statement(local_name),
                "Period Type": period_type, "Period Start": period_start, "Period End": period_end,
                "Value": value, "Unit": unit_str,
                "Decimals": getattr(fact, "decimals", "") or "",
                "Entity": entity,
                "Dimensions": "; ".join(f"{k}={v}" for k, v in dims.items()) if dims else "",
            })

        try:
            for ctx in model_xbrl.contexts.values():
                if ctx.entityIdentifier:
                    meta["entity_id"] = ctx.entityIdentifier[1]
                    break
        except Exception:
            pass

        logs.append(f"Facts found: {len(rows)}")
        model_manager.close()

    COLUMNS = ["Concept","Label","Namespace","Statement","Period Type",
               "Period Start","Period End","Value","Unit","Decimals","Entity","Dimensions"]

    if not rows:
        df = pd.DataFrame(columns=COLUMNS)
        df["_numeric"] = pd.Series(dtype=float)
        logs.append("Warning: no facts could be extracted.")
        return df, logs, meta

    df = pd.DataFrame(rows, columns=COLUMNS)
    df["Value"] = pd.to_numeric(df["Value"], errors="ignore")
    df["_numeric"] = pd.to_numeric(df["Value"], errors="coerce")
    return df, logs, meta


@st.cache_data(show_spinner=False)
def load_facts_from_file(file_bytes: bytes, file_ext: str, filename: str):
    """
    Wrapper that handles zip packages and direct xhtml/html/xml files.
    """
    import zipfile as zf
    import tempfile, os

    if file_ext == "zip":
        # Existing zip path
        return load_facts(file_bytes)
    else:
        # Direct file — write to temp, load via Arelle
        from arelle import Cntlr, ModelManager
        logs = [f"Direct file load: {filename}"]
        meta = {}

        with tempfile.TemporaryDirectory() as tmpdir:
            entry = os.path.join(tmpdir, filename)
            with open(entry, "wb") as f:
                f.write(file_bytes)

            cntlr = Cntlr.Cntlr(logFileName="logToStdErr")
            model_manager = ModelManager.initialize(cntlr)
            model_manager.skipDTS = True
            model_xbrl = model_manager.load(entry)

            if model_xbrl is None:
                raise RuntimeError("Arelle failed to load the file.")

            rows = []
            for fact in model_xbrl.facts:
                try:
                    concept = fact.concept
                    if concept is not None:
                        local_name = concept.qname.localName
                        ns = concept.qname.namespaceURI or ""
                        try:
                            label = concept.label(lang="en") or local_name
                        except Exception:
                            label = local_name
                    else:
                        local_name = fact.qname.localName
                        ns = fact.qname.namespaceURI or ""
                        label = local_name
                except Exception:
                    try:
                        local_name = fact.qname.localName
                        ns = ""
                        label = local_name
                    except Exception:
                        continue

                try:
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
                except Exception:
                    ctx = None
                    period_type = period_start = period_end = ""

                unit_str = ""
                try:
                    unit = fact.unit
                    if unit is not None:
                        unit_str = str(unit.value)
                except Exception:
                    pass

                dims = {}
                try:
                    if ctx is not None:
                        for dq, dv in ctx.qnameDims.items():
                            try:
                                dims[dq.localName] = dv.memberQname.localName if dv.isExplicit else str(dv.typedMember)
                            except Exception:
                                dims[dq.localName] = str(dv)
                except Exception:
                    pass

                value = ""
                try:
                    value = fact.value
                except Exception:
                    try:
                        value = fact.text or ""
                    except Exception:
                        pass

                entity = ""
                try:
                    if ctx and ctx.entityIdentifier:
                        entity = ctx.entityIdentifier[1]
                except Exception:
                    pass

                rows.append({
                    "Concept": local_name, "Label": label, "Namespace": ns,
                    "Statement": classify_statement(local_name),
                    "Period Type": period_type, "Period Start": period_start, "Period End": period_end,
                    "Value": value, "Unit": unit_str,
                    "Decimals": getattr(fact, "decimals", "") or "",
                    "Entity": entity,
                    "Dimensions": "; ".join(f"{k}={v}" for k, v in dims.items()) if dims else "",
                })

            try:
                for ctx in model_xbrl.contexts.values():
                    if ctx.entityIdentifier:
                        meta["entity_id"] = ctx.entityIdentifier[1]
                        break
            except Exception:
                pass

            logs.append(f"Facts found: {len(rows)}")
            model_manager.close()

        import pandas as pd
        COLUMNS = ["Concept","Label","Namespace","Statement","Period Type",
                   "Period Start","Period End","Value","Unit","Decimals","Entity","Dimensions"]

        if not rows:
            df = pd.DataFrame(columns=COLUMNS)
            df["_numeric"] = pd.Series(dtype=float)
            logs.append("Warning: no facts could be extracted.")
            return df, logs, meta

        df = pd.DataFrame(rows, columns=COLUMNS)
        df["Value"] = pd.to_numeric(df["Value"], errors="ignore")
        df["_numeric"] = pd.to_numeric(df["Value"], errors="coerce")
        return df, logs, meta
