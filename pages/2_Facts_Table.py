import streamlit as st
import pandas as pd

st.set_page_config(page_title="Facts Table – XBRL Fact Viewer", page_icon="📋", layout="wide")

st.markdown("""
<style>
    [data-testid="stSidebar"] { background: #1e3a5f; }
    [data-testid="stSidebar"] * { color: #e2e8f0 !important; }
    .section-header { font-size:1.05rem; font-weight:600; color:#1e3a5f; margin:1.5rem 0 0.75rem 0;
        padding-bottom:0.35rem; border-bottom:2px solid #e2e8f0; }
</style>
""", unsafe_allow_html=True)

if "esef_df" not in st.session_state:
    st.warning("No file loaded. Go to the **Upload** page and upload an XBRL file to begin.")
    st.stop()

df = st.session_state["esef_df"]

st.title("📋 Facts Table")
st.caption(f"Report: {st.session_state.get('esef_filename', '')} — {len(df):,} facts loaded")

# ── Sidebar filters ───────────────────────────────────────────────────────────
with st.sidebar:
    st.header("🔍 Filters")

    search = st.text_input("Search concept / label", "")

    statements = ["All"] + sorted(df["Statement"].unique().tolist())
    selected_stmt = st.selectbox("Financial Statement", statements)

    period_ends = ["All"] + sorted(df["Period End"].dropna().unique().tolist(), reverse=True)
    selected_period = st.selectbox("Period End", period_ends)

    units = ["All"] + sorted(df["Unit"].dropna().unique().tolist())
    selected_unit = st.selectbox("Unit", units)

    namespaces = ["All"] + sorted(df["Namespace"].dropna().unique().tolist())
    selected_ns = st.selectbox("Namespace", namespaces)

    show_dims_only = st.checkbox("Only facts with dimensions")
    hide_extension = st.checkbox("Hide extension concepts")

    group_by_stmt = st.checkbox("Group rows by Statement", value=True)

# ── Apply filters ─────────────────────────────────────────────────────────────
filtered = df.copy()

if search:
    mask = (
        filtered["Concept"].str.contains(search, case=False, na=False) |
        filtered["Label"].str.contains(search, case=False, na=False)
    )
    filtered = filtered[mask]

if selected_stmt != "All":
    filtered = filtered[filtered["Statement"] == selected_stmt]
if selected_period != "All":
    filtered = filtered[filtered["Period End"] == selected_period]
if selected_unit != "All":
    filtered = filtered[filtered["Unit"] == selected_unit]
if selected_ns != "All":
    filtered = filtered[filtered["Namespace"] == selected_ns]
if show_dims_only:
    filtered = filtered[filtered["Dimensions"] != ""]
if hide_extension:
    filtered = filtered[filtered["Statement"] != "Other / Extension"]

# Drop internal numeric helper column
display_cols = [c for c in filtered.columns if c != "_numeric"]
filtered_display = filtered[display_cols]

# ── Summary ───────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Facts shown", f"{len(filtered_display):,}")
c2.metric("Unique concepts", f"{filtered_display['Concept'].nunique():,}")
c3.metric("Periods", f"{filtered_display['Period End'].nunique():,}")
c4.metric("With dimensions", f"{(filtered_display['Dimensions'] != '').sum():,}")

# ── Table (optionally grouped) ────────────────────────────────────────────────
STATEMENT_ORDER = [
    "Income Statement",
    "Balance Sheet",
    "Cash Flow",
    "Other Comprehensive Income",
    "Other / Extension",
]

if group_by_stmt:
    for stmt in STATEMENT_ORDER:
        subset = filtered_display[filtered_display["Statement"] == stmt]
        if subset.empty:
            continue
        st.markdown(f'<div class="section-header">{stmt} — {len(subset):,} facts</div>', unsafe_allow_html=True)
        st.dataframe(
            subset.drop(columns=["Statement"]).reset_index(drop=True),
            use_container_width=True,
            height=min(400, 40 + len(subset) * 35),
        )
else:
    st.dataframe(filtered_display.reset_index(drop=True), use_container_width=True, height=560)

# ── Download ──────────────────────────────────────────────────────────────────
st.markdown('<div class="section-header">Download</div>', unsafe_allow_html=True)
csv = filtered_display.to_csv(index=False)
st.download_button(
    label="⬇️ Download filtered CSV",
    data=csv,
    file_name="esef_facts.csv",
    mime="text/csv",
)
