import streamlit as st
import pandas as pd
import numpy as np
import datetime
import io

# --- 1. CONFIGURATION ---
st.set_page_config(page_title="RCA Command Center", layout="wide")
st.title("🚚 Universal Daily RCA Command Center")

# Multiple File Uploader (CSV & XLSX)
uploaded_files = st.sidebar.file_uploader(
    "Upload Delivery Report(s)", 
    type=["csv", "xlsx"], 
    accept_multiple_files=True
)

# --- 2. OPTIMIZED DATA ENGINE ---
@st.cache_data(show_spinner="Loading & Optimizing Data...")
def load_and_process_data(files):
    df_list = []
    for file in files:
        if file.name.endswith('.csv'):
            temp = pd.read_csv(file, low_memory=False)
        else:
            temp = pd.read_excel(file)
        
        # --- FIXED MEMORY DOWNCASTING ---
        for col in temp.select_dtypes(include=['float', 'int']).columns:
            # 'float' tells pandas to find the smallest possible numeric size (like float32)
            temp[col] = pd.to_numeric(temp[col], downcast='float')
            
        df_list.append(temp)
    
    df = pd.concat(df_list, ignore_index=True)
    
    # Date Parsing (DD-MM-YYYY)
    time_cols = ['slot_from_time', 'order_binned_time', 'assignment_to_Cee_time', 'order_delivered_time']
    for col in time_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], dayfirst=True, errors='coerce')
    
    df['delivery_date'] = df['slot_from_time'].dt.date
    return df

@st.cache_data
def convert_to_csv(df):
    return df.to_csv(index=False).encode('utf-8')

if uploaded_files:
    # Load Master Data
    df = load_and_process_data(uploaded_files)
    
    # --- 3. DYNAMIC FILTERS ---
    available_dates = sorted(df['delivery_date'].dropna().unique())
    if not available_dates:
        st.error("No valid dates found.")
        st.stop()
        
    st.sidebar.subheader("📅 Global Filters")
    start_dt = st.sidebar.date_input("Start Date", available_dates[0])
    end_dt = st.sidebar.date_input("End Date", available_dates[-1])
    
    cities = sorted(df['city_name'].dropna().unique().tolist())
    sel_city = st.sidebar.selectbox("Select City", ["All Cities"] + cities)

    # Filtered Data
    df_f = df[(df['delivery_date'] >= start_dt) & (df['delivery_date'] <= end_dt)].copy()
    if sel_city != "All Cities":
        df_f = df_f[df_f['city_name'] == sel_city]

    if df_f.empty:
        st.warning("No records found for this selection.")
        st.stop()

    # --- 4. VECTORIZED RCA CALCULATIONS ---
    OTD_LIMIT, PICK_LIMIT = datetime.time(7, 0), datetime.time(4, 0)
    
    df_f['four_am'] = pd.to_datetime(df_f['delivery_date'].astype(str) + ' 04:00:00')
    df_f['wait_start'] = np.maximum(df_f['order_binned_time'], df_f['four_am'])
    df_f['Eff_Wait'] = (df_f['assignment_to_Cee_time'] - df_f['wait_start']).dt.total_seconds() / 60
    df_f['Travel_Mins'] = (df_f['order_delivered_time'] - df_f['assignment_to_Cee_time']).dt.total_seconds() / 60
    
    df_f['Is_Late'] = (df_f['order_delivered_time'].dt.time > OTD_LIMIT) | (df_f['order_delivered_time'].isna())

    conds = [
        (~df_f['Is_Late']),
        (df_f['order_status'].str.lower() == 'binned') & (df_f['route_id'].isna() | (df_f['route_id'] == 0)),
        (df_f['order_binned_time'].dt.time > PICK_LIMIT),
        (df_f['Eff_Wait'] > 30),
        (df_f['Travel_Mins'] > 80)
    ]
    labels = ["On-time", "CEE Unavailable", "Late GRN/Picking", "CEE Late Reporting", "CEE Took More Time"]
    df_f['Primary_RCA'] = np.select(conds, labels, default="Operational Delay")

    # --- 5. TABS ---
    tabs = st.tabs(["OTD 7 AM RCA", "City Summary", "Route Analysis", "CEE Performance", "Society Load", "Audit Log"])

    with tabs[0]: # RCA TAB
        breached = df_f[df_f['Is_Late']]
        c1, c2, c3 = st.columns(3)
        c1.metric("Breached Orders", f"{len(breached):,}")
        c2.metric("SLA Success", f"{( (1 - len(breached)/len(df_f)) * 100):.1f}%")
        c3.metric("Affected Stores", breached['sa_name'].nunique())
        st.table(breached.groupby(['city_name', 'Primary_RCA']).size().reset_index(name='Orders'))

    with tabs[1]: # CITY SUMMARY
        st.dataframe(df_f.pivot_table(index='city_name', columns='order_status', values='order_id', aggfunc='count', fill_value=0), use_container_width=True)

    with tabs[2]: # ROUTE ANALYSIS
        rt_view = df_f.groupby(['route_id', 'sa_name']).agg(Orders=('order_id', 'count'), Avg_Travel=('Travel_Mins', 'mean')).reset_index()
        st.dataframe(rt_view.sort_values('Orders', ascending=False), use_container_width=True)

    with tabs[3]: # CEE PERFORMANCE
        cee_v = df_f.groupby(['cee_name', 'sa_name']).agg(Orders=('order_id', 'count'), Late=('Is_Late', 'sum')).reset_index()
        st.dataframe(cee_v.sort_values('Late', ascending=False), use_container_width=True)

    with tabs[4]: # SOCIETY LOAD
        soc_v = df_f.groupby(['society_id', 'sa_name']).agg(Total=('order_id', 'count'), Impacted=('Is_Late', 'sum')).reset_index()
        soc_v['Impact %'] = (soc_v['Impacted'] / soc_v['Total'] * 100).round(1)
        st.dataframe(soc_v.sort_values('Total', ascending=False), use_container_width=True)

    with tabs[5]: # AUDIT LOG + DOWNLOAD
        st.subheader("Final Audit Log")
        csv_data = convert_to_csv(df_f)
        st.download_button(
            label="📥 Download Audit Data (CSV)",
            data=csv_data,
            file_name=f"rca_audit_{sel_city}_{start_dt}.csv",
            mime='text/csv'
        )
        st.dataframe(df_f[['order_id', 'sa_name', 'Primary_RCA', 'order_status', 'order_delivered_time']].head(1000), use_container_width=True)

else:
    st.info("Please upload your CSV or XLSX file(s) to start.")
