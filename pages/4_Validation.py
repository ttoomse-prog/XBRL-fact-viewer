"""
pages/4_Validation.py

Runs XULE Studio rule exports against the loaded XBRL filing.
Accepts the JSON export from XULE Studio and evaluates each rule
against the facts DataFrame already in session state.
"""
import json
import tempfile
import os
import subprocess
import streamlit as st
import pandas as pd

st.set_page_config(page_title="Validation – XBRL Fact Viewer", page_icon="✅", layout="wide")

st.markdown("""
<style>
    [data-testid="stSidebar"] { background: #1e3a5f; }
    [data-testid="stSidebar"] * { color: #e2e8f0 !important; }
</style>
""", unsafe_allow_html=True)

st.title("✅ XULE Validation")
st.caption("Run quality rules authored in XULE Studio against the loaded filing.")

# ── Guard: need a loaded filing ───────────────────────────────────────────────
if "esef_df" not in st.session_state:
    st.warning("No filing loaded. Go to the **Upload** page first.")
    st.stop()

df: pd.DataFrame = st.session_state["esef_df"]
filename = st.session_state.get("esef_filename", "unknown")
st.info(f"Filing loaded: **{filename}** — {len(df):,} facts")

# ── Rule engine helpers ───────────────────────────────────────────────────────

def strip_prefix(concept: str) -> str:
    """'uk-gaap:TotalAssets' → 'TotalAssets'"""
    return concept.split(":")[-1] if ":" in concept else concept

def get_fact_value(df: pd.DataFrame, local_name: str):
    """
    Return the numeric value for a concept local name from the DataFrame.
    Picks the most recent duration period if multiple exist, else first match.
    Prefers rows without dimensions (base facts).
    """
    matches = df[df["Concept"] == local_name].copy()
    if matches.empty:
        return None, None

    # Prefer facts without dimensions
    no_dims = matches[matches["Dimensions"] == ""]
    if not no_dims.empty:
        matches = no_dims

    # Prefer duration periods (P&L), then instant (balance sheet)
    duration = matches[matches["Period Type"] == "duration"]
    if not duration.empty:
        matches = duration

    numeric = matches[matches["_numeric"].notna()]
    if not numeric.empty:
        row = numeric.sort_values("Period End", ascending=False).iloc[0]
        return row["_numeric"], row["Period End"]

    row = matches.iloc[0]
    return row["Value"], row.get("Period End", "")


def evaluate_rule(rule: dict, df: pd.DataFrame) -> dict:
    """
    Evaluate a single XULE Studio rule against the facts DataFrame.
    Returns a result dict with keys: status, message, details.
    """
    rule_type = rule.get("ruleType", "")
    concepts = rule.get("conceptsUsed", [])
    local_names = [strip_prefix(c) for c in concepts]
    severity = rule.get("severity", "warning")

    # Resolve values for all concepts
    resolved = {}
    for ln in local_names:
        val, period = get_fact_value(df, ln)
        resolved[ln] = {"value": val, "period": period}

    present = {ln: r for ln, r in resolved.items() if r["value"] is not None}
    missing = [ln for ln in local_names if resolved[ln]["value"] is None]

    def fmt(v):
        if v is None:
            return "not found"
        try:
            return f"{float(v):,.0f}"
        except Exception:
            return str(v)

    # ── EXISTENCE ────────────────────────────────────────────────────────────
    if rule_type == "EXISTENCE":
        if not local_names:
            return {"status": "skipped", "message": "No concepts specified.", "details": {}}
        if missing:
            return {
                "status": "fail",
                "message": f"Concept(s) not found in filing: {', '.join(missing)}",
                "details": resolved,
            }
        return {
            "status": "pass",
            "message": f"All {len(local_names)} concept(s) present.",
            "details": resolved,
        }

    # ── SIGN ─────────────────────────────────────────────────────────────────
    if rule_type == "SIGN":
        if not local_names:
            return {"status": "skipped", "message": "No concepts specified.", "details": {}}
        primary = local_names[0]
        val = resolved[primary]["value"]
        if val is None:
            return {"status": "skipped", "message": f"{primary} not found in filing.", "details": resolved}
        try:
            num = float(val)
        except Exception:
            return {"status": "skipped", "message": f"{primary} value is non-numeric: {val}", "details": resolved}

        # Check xuleCode for >= 0 or <= 0
        xule = rule.get("xuleCode", "")
        if ">= 0" in xule or "> 0" in xule:
            if num < 0:
                return {"status": "fail", "message": f"{primary} is negative: {fmt(num)}", "details": resolved}
            return {"status": "pass", "message": f"{primary} = {fmt(num)} (positive ✓)", "details": resolved}
        elif "<= 0" in xule or "< 0" in xule:
            if num > 0:
                return {"status": "fail", "message": f"{primary} is positive but expected negative: {fmt(num)}", "details": resolved}
            return {"status": "pass", "message": f"{primary} = {fmt(num)} (negative ✓)", "details": resolved}
        else:
            return {"status": "pass", "message": f"{primary} = {fmt(num)}", "details": resolved}

    # ── CONDITIONAL ──────────────────────────────────────────────────────────
    if rule_type == "CONDITIONAL":
        if len(local_names) < 2:
            return {"status": "skipped", "message": "Need at least 2 concepts for conditional check.", "details": resolved}
        trigger = local_names[0]
        required = local_names[1:]
        if resolved[trigger]["value"] is None:
            return {"status": "pass", "message": f"{trigger} not reported — condition does not apply.", "details": resolved}
        missing_req = [r for r in required if resolved[r]["value"] is None]
        if missing_req:
            return {
                "status": "fail",
                "message": f"{trigger} is reported ({fmt(resolved[trigger]['value'])}) but {', '.join(missing_req)} is missing.",
                "details": resolved,
            }
        return {
            "status": "pass",
            "message": f"{trigger} reported and all required disclosures present.",
            "details": resolved,
        }

    # ── CALCULATION ──────────────────────────────────────────────────────────
    if rule_type == "CALCULATION":
        if len(local_names) < 2:
            return {"status": "skipped", "message": "Need at least 2 concepts for calculation check.", "details": resolved}

        # Try to determine total vs components from xuleCode
        # Pattern: $total == $a + $b  → first concept is total, rest are addends
        # Or: $total == $a - $b  → subtraction
        if missing:
            return {
                "status": "skipped",
                "message": f"Cannot evaluate — missing: {', '.join(missing)}",
                "details": resolved,
            }

        vals = []
        for ln in local_names:
            try:
                vals.append(float(resolved[ln]["value"]))
            except Exception:
                return {"status": "skipped", "message": f"Non-numeric value for {ln}", "details": resolved}

        total = vals[0]
        components = vals[1:]

        # Check xuleCode for subtraction
        xule = rule.get("xuleCode", "")
        if " - " in xule and len(components) == 2:
            # e.g. GrossProfit = Revenue - CostOfSales
            calc = components[0] - components[1]
        else:
            calc = sum(components)

        tolerance = max(abs(total) * 0.001, 1)  # 0.1% or 1 unit tolerance
        diff = abs(total - calc)

        detail_str = " + ".join(f"{local_names[i+1]}={fmt(v)}" for i, v in enumerate(components))
        if diff <= tolerance:
            return {
                "status": "pass",
                "message": f"{local_names[0]}={fmt(total)} matches calculation ({detail_str} = {fmt(calc)}) ✓",
                "details": resolved,
            }
        return {
            "status": "fail",
            "message": f"{local_names[0]}={fmt(total)} ≠ {detail_str} = {fmt(calc)} (difference: {fmt(diff)})",
            "details": resolved,
        }

    # ── THRESHOLD ────────────────────────────────────────────────────────────
    if rule_type == "THRESHOLD":
        if missing:
            return {"status": "skipped", "message": f"Missing concepts: {', '.join(missing)}", "details": resolved}
        return {
            "status": "manual",
            "message": "Threshold rules require manual review — see XULE code for condition.",
            "details": resolved,
        }

    # ── CONSISTENCY ──────────────────────────────────────────────────────────
    if rule_type == "CONSISTENCY":
        return {
            "status": "manual",
            "message": "Consistency rules require period-over-period data — check manually.",
            "details": resolved,
        }

    return {"status": "skipped", "message": f"Rule type '{rule_type}' not yet supported.", "details": resolved}


# ── Status styling ────────────────────────────────────────────────────────────
STATUS_STYLE = {
    "pass":    {"emoji": "✅", "color": "#166534", "bg": "#f0fdf4", "border": "#86efac", "label": "PASS"},
    "fail":    {"emoji": "❌", "color": "#991b1b", "bg": "#fef2f2", "border": "#fca5a5", "label": "FAIL"},
    "skipped": {"emoji": "⏭️", "color": "#92400e", "bg": "#fffbeb", "border": "#fcd34d", "label": "SKIPPED"},
    "manual":  {"emoji": "🔍", "color": "#1e40af", "bg": "#eff6ff", "border": "#93c5fd", "label": "MANUAL REVIEW"},
}

TYPE_COLORS = {
    "CALCULATION": "#1e3a5f", "SIGN": "#166534", "EXISTENCE": "#92400e",
    "CONDITIONAL": "#4c1d95", "THRESHOLD": "#7c2d12", "CONSISTENCY": "#134e4a",
}

# ── Upload rules ──────────────────────────────────────────────────────────────
st.markdown("### Upload rules")
st.markdown(
    "Export your ruleset from **XULE Studio** (Rule Library → Export JSON), then upload it here. "
    "Or paste individual rules as JSON below."
)

col_upload, col_paste = st.columns([1, 1])

rules = []

with col_upload:
    rules_file = st.file_uploader("Upload JSON ruleset", type=["json"], key="rules_upload")
    if rules_file:
        try:
            loaded = json.loads(rules_file.read())
            if isinstance(loaded, list):
                rules = loaded
                st.success(f"Loaded {len(rules)} rule(s)")
            else:
                st.error("Expected a JSON array of rules.")
        except Exception as e:
            st.error(f"Invalid JSON: {e}")

with col_paste:
    pasted = st.text_area("Or paste a single rule JSON", height=120, placeholder='{"ruleId": "UK.001", "ruleType": "SIGN", ...}')
    if pasted.strip():
        try:
            single = json.loads(pasted.strip())
            if isinstance(single, dict):
                rules = [single]
                st.success("Rule parsed.")
            elif isinstance(single, list):
                rules = single
                st.success(f"{len(rules)} rule(s) parsed.")
        except Exception as e:
            st.error(f"Invalid JSON: {e}")

if not rules:
    st.info("Upload a JSON ruleset or paste a rule above to run validation.")
    st.stop()

# ── Run validation ────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(f"### Results — {len(rules)} rule(s) against {filename}")

results = []
for rule in rules:
    result = evaluate_rule(rule, df)
    results.append({**rule, **result})

# Summary metrics
passes  = sum(1 for r in results if r["status"] == "pass")
fails   = sum(1 for r in results if r["status"] == "fail")
skipped = sum(1 for r in results if r["status"] in ("skipped", "manual"))

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total rules", len(results))
m2.metric("✅ Pass", passes)
m3.metric("❌ Fail", fails)
m4.metric("⏭️ Skipped / Manual", skipped)

# Filter
filter_status = st.multiselect(
    "Filter by status",
    options=["pass", "fail", "skipped", "manual"],
    default=["pass", "fail", "skipped", "manual"],
)

shown = [r for r in results if r.get("status") in filter_status]

# Results cards
for r in shown:
    status = r.get("status", "skipped")
    s = STATUS_STYLE.get(status, STATUS_STYLE["skipped"])
    rule_type = r.get("ruleType", "")
    type_color = TYPE_COLORS.get(rule_type, "#374151")

    with st.container():
        st.markdown(f"""
        <div style="border:1px solid {s['border']}; border-left: 4px solid {s['border']};
                    background:{s['bg']}; border-radius:8px; padding:14px 18px; margin-bottom:10px;">
            <div style="display:flex; align-items:center; gap:10px; margin-bottom:6px; flex-wrap:wrap;">
                <code style="background:#e5e7eb; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:600;">{r.get('ruleId','')}</code>
                <span style="font-weight:600; font-size:14px; color:{s['color']};">{s['emoji']} {s['label']}</span>
                <span style="font-size:11px; padding:2px 8px; border-radius:4px; background:{type_color}; color:#fff; font-weight:500;">{rule_type}</span>
                <span style="font-size:11px; color:#6b7280;">severity: {r.get('severity','')}</span>
            </div>
            <div style="font-size:13px; font-weight:500; color:#111827; margin-bottom:4px;">{r.get('ruleName','')}</div>
            <div style="font-size:12px; color:#4b5563; font-style:italic; margin-bottom:8px;">"{r.get('plainEnglishSummary','')}"</div>
            <div style="font-size:13px; color:{s['color']};">{r.get('message','')}</div>
        </div>
        """, unsafe_allow_html=True)

        # Expandable: concept values + XULE code
        with st.expander("Details"):
            details = r.get("details", {})
            if details:
                st.markdown("**Concept values found in filing:**")
                det_rows = []
                for concept, info in details.items():
                    val = info.get("value")
                    period = info.get("period", "")
                    try:
                        display_val = f"{float(val):,.0f}" if val is not None else "⚠️ not found"
                    except Exception:
                        display_val = str(val) if val is not None else "⚠️ not found"
                    det_rows.append({"Concept": concept, "Value": display_val, "Period": period or ""})
                st.dataframe(pd.DataFrame(det_rows), use_container_width=True, hide_index=True)

            xule = r.get("xuleCode", "")
            if xule:
                st.markdown("**XULE code:**")
                st.code(xule, language="text")

            if r.get("caveats"):
                st.caption(f"⚠️ Caveats: {r['caveats']}")

# ── Download results ──────────────────────────────────────────────────────────
st.markdown("---")
export_rows = []
for r in results:
    export_rows.append({
        "Rule ID": r.get("ruleId",""),
        "Rule Name": r.get("ruleName",""),
        "Rule Type": r.get("ruleType",""),
        "Severity": r.get("severity",""),
        "Status": r.get("status","").upper(),
        "Message": r.get("message",""),
        "Summary": r.get("plainEnglishSummary",""),
    })

export_df = pd.DataFrame(export_rows)
csv = export_df.to_csv(index=False).encode()

st.download_button(
    "⬇️ Download validation results (CSV)",
    data=csv,
    file_name=f"xule-validation-{filename}.csv",
    mime="text/csv",
)

st.caption(
    "Rules evaluated using a Python rule engine against the extracted facts. "
    "For full XULE compilation and taxonomy-aware validation, run via Arelle + XULE plugin locally."
)
