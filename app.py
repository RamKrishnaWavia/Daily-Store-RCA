import streamlit as st
import pandas as pd
import datetime

# --- 1. INITIAL SETUP ---
st.set_page_config(page_title="Personal Command Center", layout="wide")
st.title("🚚 Daily Delivery & RCA Command Center")

# Uploader for multiple files (CSV or XLSX)
uploaded_files = st.sidebar.file_uploader(
    "Upload Delivery Report(s)", 
    type=["csv", "xlsx"], 
    accept_multiple_files=True
)

if uploaded_files:
    # --- LOAD & MERGE DATA ---
    df_list = []
    for file in uploaded_files:
        if file.name.endswith('.csv'):
            df_list.append(pd.read_csv(file))
        else:
            df_list.append(pd.read_excel(file))
    
    df = pd.concat(df_list, ignore_index=True)
    
    # --- DATE PARSING (DD-MM-YYYY) ---
    time_cols = ['slot_from_time', 'order_binned_time', 'assignment_to_Cee_time', 'order_delivered_time']
    for col in time_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], dayfirst=True, errors='coerce')

    df['delivery_date'] = df['slot_from_time'].dt.date
    available_dates = sorted(df['delivery_date'].dropna().unique())
    
    if not available_dates:
        st.error("No valid dates found. Check 'slot_from_time' column.")
        st.stop()
        
    f_min, f_max = available_dates[0], available_dates[-1]

    # --- 2. FILTERS ---
    st.sidebar.subheader("📅 Global Filters")
    start_dt = st.sidebar.date_input("Start Date", f_min)
    end_dt = st.sidebar.date_input("End Date", f_max)
    
    cities = sorted(df['city_name'].dropna().unique().tolist())
    sel_city = st.sidebar.selectbox("Select City", ["All Cities"] + cities)

    mask = (df['delivery_date'] >= start_dt) & (df['delivery_date'] <= end_dt)
    df_f = df[mask].copy()
    if sel_city != "All Cities":
        df_f = df_f[df_f['city_name'] == sel_city]

    if df_f.empty:
        st.warning("No records found for the selection.")
        st.stop()

    # --- 3. STATUS & TIMING LOGIC ---
    def get_live_status(row):
        s = str(row['order_status']).strip().lower()
        if s in ['cancelled', 'payment_pending']: return 'Cancelled'
        if s in ['complete', 'delivered']: return 'Delivered'
        if s in ['reached']: return 'Reached'
        if s in ['ready_to_ship', 'ofd', 'dispatched']: return 'OFD'
        return 'Bin'

    df_f['Live_Status'] = df_f.apply(get_live_status, axis=1)
    df_act = df_f[df_f['Live_Status'] != 'Cancelled'].copy()

    # Constants
    OTD_LIMIT, PICK_LIMIT = datetime.time(7, 0), datetime.time(4, 0)

    # Timing Calculations
    df_act['four_am'] = pd.to_datetime(df_act['delivery_date'].astype(str) + ' 04:00:00')
    df_act['wait_start'] = df_act[['order_binned_time', 'four_am']].max(axis=1)
    df_act['Eff_Wait'] = (df_act['assignment_to_Cee_time'] - df_act['wait_start']).dt.total_seconds() / 60
    df_act['Travel_Mins'] = (df_act['order_delivered_time'] - df_act['assignment_to_Cee_time']).dt.total_seconds() / 60

    # Breach Flag: Delivered after 7 AM OR Still Open/Not Delivered
    df_act['Is_Late'] = (df_act['order_delivered_time'].dt.time > OTD_LIMIT) | (df_act['order_delivered_time'].isna())
    
    # --- 4. REFINED RCA ENGINE ---
    def calculate_rca(row):
        if not row['Is_Late']: return "On-time"
        
        # 1. CEE Unavailable: Status is binned AND route_id is blank/0
        is_binned_status = str(row['order_status']).strip().lower() == 'binned'
        no_route = pd.isna(row['route_id']) or row['route_id'] == 0 or str(row['route_id']).strip() == ''
        if is_binned_status and no_route:
            return "CEE Unavailable"
        
        # 2. Late GRN/Picking: Order binned after 4 AM
        if pd.notnull(row['order_binned_time']) and row['order_binned_time'].time() > PICK_LIMIT:
            return "Late GRN/Picking"
        
        # 3. CEE Late Reporting: Rider assigned > 30 mins after ready
        if row['Eff_Wait'] > 30:
            return "CEE Late Reporting"
        
        # 4. CEE Took More Time: Travel > 2 hours
        if pd.notnull(row['Travel_Mins']) and row['Travel_Mins'] > 120:
            return "CEE Took More Time"
            
        return "Operational Delay"

    df_act['Primary_RCA'] = df_act.apply(calculate_rca, axis=1)

    # --- 5. DASHBOARD TABS ---
    t_sla, t_city, t_rt, t_cee, t_soc, t_od = st.tabs([
        "OTD 7 AM SLA Breached RCA", "City Summary", "Route Analysis", "CEE Performance", "Society Load", "Audit Log"
    ])

    with t_sla:
        st.subheader("📉 OTD 7 AM SLA Breach Analysis")
        breached = df_act[df_act['Is_Late']]
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Breached Orders", f"{len(breached):,}")
        m2.metric("SLA Success Rate", f"{( (1 - len(breached)/len(df_act)) * 100):.1f}%")
        m3.metric("Stores with Breaches", breached['sa_name'].nunique())
        
        st.divider()
        impact = breached.groupby(['city_name', 'Primary_RCA']).agg(Orders=('order_id', 'count'), Routes=('route_id', 'nunique')).reset_index()
        st.table(impact.sort_values(['city_name', 'Orders'], ascending=[True, False]))

    with t_city:
        st.subheader("City Status Snapshot")
        city_pivot = df_f.pivot_table(index='city_name', columns='Live_Status', values='order_id', aggfunc='count', fill_value=0).reset_index()
        st.dataframe(city_pivot, width='stretch')

    with t_rt:
        st.subheader("Route Productivity Stats")
        rt_view = df_act.groupby(['route_id', 'sa_name']).agg(Orders=('order_id', 'count'), Societies=('society_id', 'nunique'), Travel=('Travel_Mins', 'mean')).reset_index()
        r1, r2, r3 = st.columns(3)
        r1.metric("Avg Orders/Route", f"{int(rt_view['Orders'].mean())}")
        r2.metric("Avg Societies/Route", f"{rt_view['Societies'].mean():.1f}")
        r3.metric("Avg Travel Time", f"{int(rt_view['Travel'].mean())}m")
        st.dataframe(rt_view.sort_values('Orders', ascending=False), width='stretch')

    with t_cee:
        st.subheader("CEE Efficiency")
        cee_v = df_act.groupby(['sa_name', 'cee_name']).agg(Trips=('route_id', 'nunique'), Orders=('order_id', 'count')).reset_index()
        st.dataframe(cee_v.sort_values('Trips', ascending=False), width='stretch')

    with t_soc:
        st.subheader("Society Load Analysis")
        soc_v = df_act.groupby(['society_id', 'sa_name']).agg(Total=('order_id', 'count'), Impacted=('Is_Late', 'sum')).reset_index()
        soc_v['Impact %'] = (soc_v['Impacted'] / soc_v['Total'] * 100).round(1)
        st.dataframe(soc_v.sort_values('Total', ascending=False), width='stretch')

    with t_od:
        st.subheader("Audit Log")
        st.dataframe(df_act[['order_id', 'sa_name', 'cee_name', 'Primary_RCA', 'Live_Status']], width='stretch')

else:
    st.info("Please upload your CSV or XLSX file(s) to begin.")
