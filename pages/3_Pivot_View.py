import streamlit as st
import pandas as pd

st.set_page_config(page_title="Pivot View – XBRL Fact Viewer", page_icon="🔀", layout="wide")

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

st.title("🔀 Pivot View")
st.markdown("Pivot monetary facts so that **concepts appear as rows** and **periods as columns** — "
            "useful for quickly comparing figures across reporting years.")

# ── Sidebar config ────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Pivot Options")

    statements = ["All"] + sorted(df["Statement"].unique().tolist())
    selected_stmt = st.selectbox("Filter by Statement", statements)

    units = ["All"] + sorted(df["Unit"].dropna().unique().tolist())
    selected_unit = st.selectbox("Filter by Unit", units)

    dims_mode = st.radio(
        "Dimensioned facts",
        ["Exclude (show undimensioned only)", "Include all", "Only dimensioned"],
        index=0,
    )

    show_labels = st.checkbox("Show labels instead of concept names", value=True)
    hide_extension = st.checkbox("Hide extension concepts", value=False)

# ── Build pivot ───────────────────────────────────────────────────────────────
numeric = df[df["_numeric"].notna()].copy()

if selected_stmt != "All":
    numeric = numeric[numeric["Statement"] == selected_stmt]
if selected_unit != "All":
    numeric = numeric[numeric["Unit"] == selected_unit]
if dims_mode == "Exclude (show undimensioned only)":
    numeric = numeric[numeric["Dimensions"] == ""]
elif dims_mode == "Only dimensioned":
    numeric = numeric[numeric["Dimensions"] != ""]
if hide_extension:
    numeric = numeric[numeric["Statement"] != "Other / Extension"]

if numeric.empty:
    st.info("No numeric facts match the current filters.")
    st.stop()

# Use label or concept name as row identifier
name_col = "Label" if show_labels else "Concept"

# Period column: prefer Period End; for instant use Period End, for duration show range
def period_label(row):
    if row["Period Type"] == "instant":
        return row["Period End"]
    elif row["Period Type"] == "duration":
        return f"{row['Period Start']} → {row['Period End']}"
    return row["Period End"] or "—"

numeric = numeric.copy()
numeric["_period_label"] = numeric.apply(period_label, axis=1)

# If dimensions present, append them to the concept name to disambiguate
if dims_mode == "Include all":
    numeric["_row_key"] = numeric.apply(
        lambda r: r[name_col] + (f" [{r['Dimensions']}]" if r["Dimensions"] else ""),
        axis=1
    )
else:
    numeric["_row_key"] = numeric[name_col]

# Aggregate (take first value if duplicates — shouldn't be many)
pivot_df = (
    numeric.groupby(["_row_key", "_period_label"])["_numeric"]
    .first()
    .unstack("_period_label")
    .reset_index()
    .rename(columns={"_row_key": "Concept / Label"})
)

# Sort columns chronologically where possible
date_cols = [c for c in pivot_df.columns if c != "Concept / Label"]
try:
    # Sort by the last date in each column label
    date_cols_sorted = sorted(date_cols, key=lambda x: x.split("→")[-1].strip())
except Exception:
    date_cols_sorted = date_cols

pivot_df = pivot_df[["Concept / Label"] + date_cols_sorted]

# Add statement column for context
concept_to_stmt = df.drop_duplicates("Concept").set_index("Concept")["Statement"].to_dict()
label_to_concept = df.drop_duplicates("Label").set_index("Label")["Concept"].to_dict()

def get_stmt(row_key):
    # Try direct concept lookup first
    clean = row_key.split(" [")[0]  # strip dimension suffix
    if clean in concept_to_stmt:
        return concept_to_stmt[clean]
    if clean in label_to_concept:
        return concept_to_stmt.get(label_to_concept[clean], "")
    return ""

pivot_df.insert(1, "Statement", pivot_df["Concept / Label"].apply(get_stmt))

st.markdown(f'<div class="section-header">Pivot — {len(pivot_df):,} concepts × {len(date_cols_sorted)} period(s)</div>', unsafe_allow_html=True)

# Format numbers nicely in display
def fmt(v):
    if pd.isna(v):
        return ""
    if abs(v) >= 1_000_000:
        return f"{v:,.0f}"
    return f"{v:,.2f}"

display_pivot = pivot_df.copy()
for col in date_cols_sorted:
    display_pivot[col] = display_pivot[col].apply(lambda v: fmt(v) if pd.notna(v) else "")

st.dataframe(display_pivot, use_container_width=True, height=580)

# ── Download ──────────────────────────────────────────────────────────────────
csv = pivot_df.to_csv(index=False)
st.download_button(
    label="⬇️ Download pivot CSV",
    data=csv,
    file_name="esef_pivot.csv",
    mime="text/csv",
)
