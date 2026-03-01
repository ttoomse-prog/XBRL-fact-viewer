import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

st.set_page_config(page_title="Dashboard – XBRL Fact Viewer", page_icon="📊", layout="wide")

st.markdown("""
<style>
    [data-testid="stSidebar"] { background: #1e3a5f; }
    [data-testid="stSidebar"] * { color: #e2e8f0 !important; }
    .metric-card { background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:1rem 1.25rem; }
    .metric-value { font-size:1.75rem; font-weight:700; color:#1e3a5f; line-height:1.1; }
    .metric-label { font-size:0.78rem; color:#64748b; text-transform:uppercase; letter-spacing:0.05em; margin-top:0.25rem; }
    .section-header { font-size:1.05rem; font-weight:600; color:#1e3a5f; margin:1.5rem 0 0.75rem 0; padding-bottom:0.35rem; border-bottom:2px solid #e2e8f0; }
</style>
""", unsafe_allow_html=True)

COLORS = ["#1e3a5f", "#2563eb", "#38bdf8", "#64748b", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6"]


def fmt_num(v):
    if pd.isna(v):
        return "—"
    if abs(v) >= 1_000_000_000:
        return f"{v/1_000_000_000:.2f}bn"
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.1f}m"
    if abs(v) >= 1_000:
        return f"{v/1_000:.0f}k"
    return f"{v:,.0f}"


def metric_card(label, value):
    st.markdown(f"""
    <div class="metric-card">
        <div class="metric-value">{value}</div>
        <div class="metric-label">{label}</div>
    </div>""", unsafe_allow_html=True)


if "esef_df" not in st.session_state:
    st.warning("No file loaded. Go to the **Upload** page and upload an XBRL file to begin.")
    st.stop()

df = st.session_state["esef_df"]
meta = st.session_state.get("esef_meta", {})
filename = st.session_state.get("esef_filename", "")

st.title("📊 Dashboard")
if filename:
    st.caption(f"Report: {filename}")

# ── Sidebar period selector ───────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Options")
    periods = sorted(df["Period End"].dropna().unique().tolist(), reverse=True)
    if periods:
        selected_period = st.selectbox("Reporting period (Period End)", ["All"] + periods)
    else:
        selected_period = "All"
    currency_filter = st.selectbox(
        "Currency / Unit",
        ["All"] + sorted(df["Unit"].dropna().unique().tolist())
    )

dff = df.copy()
if selected_period != "All":
    dff = dff[dff["Period End"] == selected_period]
if currency_filter != "All":
    dff = dff[dff["Unit"] == currency_filter]

numeric = dff[dff["_numeric"].notna()].copy()
monetary = numeric[numeric["Unit"].str.len() == 3] if not numeric.empty else numeric  # ISO currency codes are 3 chars

# ── Headline metrics ──────────────────────────────────────────────────────────
st.markdown('<div class="section-header">Overview</div>', unsafe_allow_html=True)
c1, c2, c3, c4, c5 = st.columns(5)

total_facts = len(dff)
unique_concepts = dff["Concept"].nunique()
monetary_facts = len(monetary)
dimensioned = (dff["Dimensions"] != "").sum()
extension_facts = (dff["Statement"] == "Other / Extension").sum()

with c1: metric_card("Total Facts", f"{total_facts:,}")
with c2: metric_card("Unique Concepts", f"{unique_concepts:,}")
with c3: metric_card("Monetary Facts", f"{monetary_facts:,}")
with c4: metric_card("With Dimensions", f"{dimensioned:,}")
with c5: metric_card("Extension Facts", f"{extension_facts:,}")

# ── Try to surface key financials ─────────────────────────────────────────────
st.markdown('<div class="section-header">Key Financial Figures</div>', unsafe_allow_html=True)

KPI_CONCEPTS = {
    "Revenue": ["Revenue", "RevenueFromContractsWithCustomers"],
    "Gross Profit": ["GrossProfit"],
    "Operating Profit": ["ProfitLossFromOperatingActivities", "OperatingIncomeLoss"],
    "Net Profit": ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    "Total Assets": ["Assets"],
    "Total Equity": ["Equity", "EquityAttributableToOwnersOfParent"],
}

kpi_cols = st.columns(len(KPI_CONCEPTS))
for col, (kpi_label, candidates) in zip(kpi_cols, KPI_CONCEPTS.items()):
    val = None
    for c in candidates:
        matches = monetary[monetary["Concept"] == c]
        if not matches.empty:
            # prefer the most recent period end, no dimensions
            no_dim = matches[matches["Dimensions"] == ""]
            subset = no_dim if not no_dim.empty else matches
            subset = subset.sort_values("Period End", ascending=False)
            raw = subset.iloc[0]["_numeric"]
            if pd.notna(raw):
                val = raw
                break
    with col:
        metric_card(kpi_label, fmt_num(val) if val is not None else "—")

# ── Charts row 1 ──────────────────────────────────────────────────────────────
st.markdown('<div class="section-header">Fact Distribution</div>', unsafe_allow_html=True)
ch1, ch2 = st.columns(2)

with ch1:
    stmt_counts = dff["Statement"].value_counts().reset_index()
    stmt_counts.columns = ["Statement", "Count"]
    fig = px.pie(
        stmt_counts, names="Statement", values="Count",
        title="Facts by Financial Statement",
        color_discrete_sequence=COLORS,
        hole=0.4,
    )
    fig.update_layout(margin=dict(t=40, b=10, l=10, r=10), height=320, showlegend=True,
                      legend=dict(orientation="v", x=1, y=0.5))
    fig.update_traces(textinfo="percent+label")
    st.plotly_chart(fig, use_container_width=True)

with ch2:
    ns_counts = dff["Namespace"].value_counts().head(8).reset_index()
    ns_counts.columns = ["Namespace", "Count"]
    # Shorten namespace URIs for display
    ns_counts["NS Short"] = ns_counts["Namespace"].apply(
        lambda x: x.split("/")[-1] if "/" in x else x
    )
    fig2 = px.bar(
        ns_counts, x="Count", y="NS Short", orientation="h",
        title="Facts by Namespace (top 8)",
        color_discrete_sequence=[COLORS[1]],
    )
    fig2.update_layout(margin=dict(t=40, b=10, l=10, r=10), height=320,
                       yaxis_title="", xaxis_title="Fact count",
                       yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig2, use_container_width=True)

# ── Charts row 2 ──────────────────────────────────────────────────────────────
ch3, ch4 = st.columns(2)

with ch3:
    period_counts = dff[dff["Period End"] != ""].groupby("Period End").size().reset_index(name="Facts")
    period_counts = period_counts.sort_values("Period End")
    fig3 = px.bar(
        period_counts, x="Period End", y="Facts",
        title="Facts by Period End",
        color_discrete_sequence=[COLORS[0]],
    )
    fig3.update_layout(margin=dict(t=40, b=10, l=10, r=10), height=300,
                       xaxis_title="", yaxis_title="Fact count")
    st.plotly_chart(fig3, use_container_width=True)

with ch4:
    # Top concepts by frequency
    top_concepts = dff["Concept"].value_counts().head(12).reset_index()
    top_concepts.columns = ["Concept", "Count"]
    fig4 = px.bar(
        top_concepts, x="Count", y="Concept", orientation="h",
        title="Most Frequent Concepts",
        color_discrete_sequence=[COLORS[2]],
    )
    fig4.update_layout(margin=dict(t=40, b=10, l=10, r=10), height=360,
                       yaxis_title="", xaxis_title="Occurrences",
                       yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig4, use_container_width=True)

# ── Monetary facts chart ───────────────────────────────────────────────────────
if not monetary.empty:
    st.markdown('<div class="section-header">Monetary Facts — Top Values</div>', unsafe_allow_html=True)
    top_monetary = (
        monetary[monetary["Dimensions"] == ""]
        .groupby("Concept")["_numeric"]
        .max()
        .abs()
        .sort_values(ascending=False)
        .head(15)
        .reset_index()
    )
    top_monetary.columns = ["Concept", "Absolute Value"]
    fig5 = px.bar(
        top_monetary, x="Absolute Value", y="Concept", orientation="h",
        title="Top 15 Monetary Facts by Absolute Value (undimensioned)",
        color_discrete_sequence=[COLORS[0]],
    )
    fig5.update_layout(margin=dict(t=40, b=10, l=10, r=10), height=400,
                       yaxis_title="", xaxis_title="Value",
                       yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig5, use_container_width=True)
