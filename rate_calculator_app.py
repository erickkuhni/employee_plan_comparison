import streamlit as st
import pandas as pd
from io import BytesIO

st.set_page_config(page_title="Simple Quote Calculator", page_icon="💡")

# Make the page wider so the table can breathe
st.markdown(
    """
    <style>
      .block-container { max-width: 1600px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---- Login page header (visible before auth) ----
st.markdown(
    """
    <div style="text-align:center; margin-top: 12px; margin-bottom: 18px;">
      <h1 style="margin-bottom: 0;">Arlington Scientific – 2026 Employee Health Insurance Decision</h1>
      <p style="font-size: 16px; color: #666; margin-top: 6px;">
        Please log in using the credentials provided to you.
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)

# --------------------------
# AUTH (Secrets + local fallback)
# --------------------------
try:
    USERS = st.secrets["users"]  # expects [users] in .streamlit/secrets.toml
except Exception:
    USERS = {"demo": "demo"}  # local dev fallback

def login_area():
    st.sidebar.header("Login")
    username = st.sidebar.text_input("Username", value="demo" if "demo" in USERS else "")
    password = st.sidebar.text_input("Password", type="password", value="demo" if "demo" in USERS else "")
    if st.sidebar.button("Login"):
        if username in USERS and USERS[username] == password:
            st.session_state["auth"] = True
            st.session_state["user"] = username
            st.rerun()
        else:
            st.sidebar.error("Incorrect username or password")

def logout():
    if st.sidebar.button("Logout"):
        st.session_state.pop("auth", None)
        st.session_state.pop("user", None)
        st.rerun()

if "auth" not in st.session_state:
    login_area()
    st.stop()

st.sidebar.success(f"Welcome, {st.session_state['user']}")
logout()

# --------------------------
# DATA LOAD
# --------------------------
RATES_XLSX = "arlington_scientific_rates_2026.xlsx"  # keep this alongside the .py

try:
    rates = pd.read_excel(RATES_XLSX, sheet_name="raw_rates")
    benefits = pd.read_excel(RATES_XLSX, sheet_name="raw_benefits")
except FileNotFoundError:
    st.error(f"Could not find {RATES_XLSX} in this folder.")
    st.stop()
except ValueError as e:
    st.error(f"Sheet missing: {e}")
    st.stop()

# Normalize columns
rates.columns = [str(c).strip() for c in rates.columns]
benefits.columns = [str(c).strip() for c in benefits.columns]

if "Age" not in rates.columns:
    st.error("The 'raw_rates' sheet must have a column named 'Age'.")
    st.stop()

# Ensure Age numeric & clean
rates["Age"] = pd.to_numeric(rates["Age"], errors="coerce")
rates = rates.dropna(subset=["Age"]).astype({"Age": int})

# Plan names = all columns except Age, preserving order
plan_names = [c for c in rates.columns if c != "Age"]

# --------------------------
# BENEFITS PRE-CLEAN (robust against NBSP/whitespace)
# --------------------------
def _norm_cell(x):
    """Normalize to remove NBSP and surrounding whitespace; None for blanks."""
    if pd.isna(x):
        return None
    if isinstance(x, str):
        s = x.replace("\u00A0", " ").strip()
        return s if s != "" else None
    return x

# First column is row labels; normalize everything, then drop fully-blank rows
benefits = benefits.rename(columns={benefits.columns[0]: "Benefit"})
benefits = benefits.applymap(_norm_cell).dropna(how="all")

# Set index and clean label strings
benefits.set_index("Benefit", inplace=True)
benefits.index = benefits.index.map(lambda s: (s.replace("\u00A0", " ").strip()) if isinstance(s, str) else "")
benefits = benefits[benefits.index != ""]  # drop rows with blank labels

# Align benefit columns to plan order; replace remaining NaNs with ""
benefits = benefits.reindex(columns=plan_names).fillna("")

# --------------------------
# UI
# --------------------------
st.title("Employee Health Plan Rate Comparison")
st.caption(
    "Monthly premium for all plans (Employee + Spouse + up to three children under 21, "
    "**plus all dependents 21–25**), plus key benefits."
)

with st.form("inputs"):
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("List all members ages on 1/1/2026")
        employee_age = st.number_input("Employee Age", min_value=0, max_value=90, value=40)
        children_input = st.text_input("Children Ages (comma separated)", "17,14,10,8")
    with col2:
        st.subheader('')
        spouse_age = st.number_input("Spouse Age (0 if none)", min_value=0, max_value=90, value=0)

    submitted = st.form_submit_button("Calculate")

# --------------------------
# HELPERS
# --------------------------
def lookup_rate_for(plan_col: str, age: int) -> float:
    """Exact age lookup. Returns 0 if not found or age <= 0."""
    if age <= 0:
        return 0.0
    row = rates.loc[rates["Age"] == age, plan_col]
    return float(row.iloc[0]) if not row.empty else 0.0

def parse_children(s: str) -> list[int]:
    if not s.strip():
        return []
    out = []
    for chunk in s.split(","):
        chunk = chunk.replace("\u00A0", " ").strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except ValueError:
            pass  # ignore non-numeric tokens
    return out

def total_for_plan(plan_col: str, emp_age: int, spouse_age: int, child_ages: list[int]) -> float:
    """
    Employee + Spouse +
    (up to 3 of the youngest children under 21) +
    (all dependents age 21–25, no cap)
    """
    total = 0.0

    # Employee + spouse
    total += lookup_rate_for(plan_col, emp_age)
    if spouse_age > 0:
        total += lookup_rate_for(plan_col, spouse_age)

    # Dependents: minors <21 (cap 3, youngest) + adults 21–25 (no cap)
    minors = sorted([a for a in child_ages if 0 <= a < 21])[:3]
    adults_21_25 = [a for a in child_ages if 21 <= a <= 25]

    for a in minors:
        total += lookup_rate_for(plan_col, a)
    for a in adults_21_25:
        total += lookup_rate_for(plan_col, a)

    if total / 2 > 600:
        employee_cost = total - 600
    else:
        employee_cost = total * 0.5

    return employee_cost

def currency(x: float) -> str:
    return f"${x:,.0f}"

def auto_df_height(n_rows: int, row_px: int = 36, header_px: int = 38, pad_px: int = 12, max_px: int = 800) -> int:
    """
    Compute a tight height for st.dataframe so Streamlit doesn't render filler blank rows.
    """
    return min(max_px, header_px + n_rows * row_px + pad_px)

# --------------------------
# CALC + TABLE BUILD
# --------------------------
if submitted:
    kids = parse_children(children_input)

    # 1) Premium row for all plans
    premium_row = {p: total_for_plan(p, int(employee_age), int(spouse_age), kids) for p in plan_names}
    df_premium = pd.DataFrame(premium_row, index=["Monthly Premium"])

    # 2) Combined: premium + benefits
    combined = pd.concat([df_premium, benefits], axis=0)

    # 3) Strict blank-row filter (post-clean). Keep rows where any plan col is non-empty after strip.
    def _is_nonblank_cell(x):
        if pd.isna(x):
            return False
        if isinstance(x, str):
            return x.replace("\u00A0", " ").strip() != ""
        return True  # numbers / other types

    mask = combined[plan_names].applymap(_is_nonblank_cell).any(axis=1)
    mask.loc["Monthly Premium"] = True  # always keep premium row

    # ensure index labels are not blank/whitespace
    valid_index = pd.Series(
        [(str(i).replace("\u00A0", " ").strip() != "") for i in combined.index],
        index=combined.index
    )

    combined_trimmed = combined[mask & valid_index]

    # Show in-app with currency on premium row; dynamic height to avoid padded blanks
    combined_to_show = combined_trimmed.copy()
    combined_to_show.loc["Monthly Premium"] = combined_to_show.loc["Monthly Premium"].apply(currency)

    st.subheader("Plan Comparison")
    st.dataframe(
        combined_to_show,
        use_container_width=True,
        height=auto_df_height(combined_to_show.shape[0])
    )

    # Inputs reference with explicit counts
    minors_used = sorted([a for a in kids if 0 <= a < 21])[:3]
    adults_used = [a for a in kids if 21 <= a <= 25]

    # --------------------------
    # Excel download (no freeze panes, no autofilter)
    # --------------------------
    buf = BytesIO()
    export_df = combined_trimmed.copy()
    export_view = export_df.reset_index().rename(columns={"index": "Benefit / Row"})

    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        export_view.to_excel(writer, index=False, sheet_name="Comparison")
        wb  = writer.book
        ws  = writer.sheets["Comparison"]

        # Formats
        fmt_header = wb.add_format({"bold": True, "text_wrap": True, "valign": "vcenter"})
        fmt_bold   = wb.add_format({"bold": True})
        fmt_wrap   = wb.add_format({"text_wrap": True})
        fmt_money  = wb.add_format({"num_format": "$#,##0", "bold": True})
        fmt_text   = wb.add_format({})

        # Header style
        ws.set_row(0, 20, fmt_header)

        # Column widths
        ws.set_column(0, 0, 32, fmt_wrap)  # labels column
        for col_idx in range(1, 1 + len(plan_names)):
            ws.set_column(col_idx, col_idx, 18, fmt_wrap)

        # Currency styling for Monthly Premium row
        mp_row_idx = export_view.index[export_view["Benefit / Row"] == "Monthly Premium"]
        if not mp_row_idx.empty:
            xlsx_row = mp_row_idx[0] + 1  # +1 for header row
            ws.write(xlsx_row, 0, "Monthly Premium", fmt_bold)
            for col_idx in range(1, 1 + len(plan_names)):
                val = export_view.iat[mp_row_idx[0], col_idx]
                try:
                    ws.write_number(xlsx_row, col_idx, float(val), fmt_money)
                except Exception:
                    ws.write(xlsx_row, col_idx, val, fmt_text)

    st.download_button(
        "Download comparison as Excel",
        data=buf.getvalue(),
        file_name="plan_comparison_with_benefits.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
