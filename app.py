import streamlit as st
import pandas as pd
import datetime

# --- 1. INITIAL SETUP & CONFIG ---
st.set_page_config(page_title="NOI Command Center", layout="wide")
st.title("🚚 Daily Delivery RCA Dashboard")

uploaded_file = st.sidebar.file_uploader("Upload Express Order Report", type="csv")

if uploaded_file:
    # Load and Parse Data
    df = pd.read_csv(uploaded_file)
    time_cols = ['slot_from_time', 'order_binned_time', 'assignment_to_Cee_time', 'order_delivered_time']
    for col in time_cols:
        df[col] = pd.to_datetime(df[col], errors='coerce')
    
    # --- 2. DYNAMIC FILTERS ---
    df['delivery_date'] = df['slot_from_time'].dt.date
    min_date = df['delivery_date'].dropna().min()
    max_date = df['delivery_date'].dropna().max()
    
    st.sidebar.subheader("Apply Filters")
    start_dt = st.sidebar.date_input("Start Date", min_date)
    end_dt = st.sidebar.date_input("End Date", max_date)
    
    cities = sorted(df['city_name'].dropna().unique().tolist())
    sel_city = st.sidebar.selectbox("Select City", ["All Cities"] + cities)

    # Filtering Logic (Reactive to selection)
    mask = (df['delivery_date'] >= start_dt) & (df['delivery_date'] <= end_dt)
    df_f = df[mask].copy()
    if sel_city != "All Cities":
        df_f = df_f[df_f['city_name'] == sel_city]

    if df_f.empty:
        st.warning("No records found for the selected date/city.")
        st.stop()

    # --- 3. OPERATIONAL LOGIC & CALCULATIONS ---
    
    # Status Mapping
    def get_live_status(row):
        s = str(row['order_status']).strip().lower()
        if s in ['cancelled', 'payment_pending']: return 'Cancelled'
        if s in ['complete', 'delivered']: return 'Delivered'
        if s in ['reached']: return 'Reached'
        if s in ['ready_to_ship', 'ofd', 'dispatched']: return 'OFD'
        return 'Bin'

    df_f['Live_Status'] = df_f.apply(get_live_status, axis=1)
    df_act = df_f[df_f['Live_Status'] != 'Cancelled'].copy()

    # Constants & Timing
    OTD_LIMIT = datetime.time(7, 0)
    PICK_LIMIT = datetime.time(4, 0)
    REPORT_CUTOFF = datetime.time(4, 30)

    df_act['four_am'] = pd.to_datetime(df_act['delivery_date'].astype(str) + ' 04:00:00')
    df_act['wait_start'] = df_act[['order_binned_time', 'four_am']].max(axis=1)
    df_act['Eff_Wait'] = (df_act['assignment_to_Cee_time'] - df_act['wait_start']).dt.total_seconds() / 60
    df_act['Travel_Mins'] = (df_act['order_delivered_time'] - df_act['assignment_to_Cee_time']).dt.total_seconds() / 60

    # CEE First-Assignment (Late Reporting Logic)
    cee_day = df_act.groupby(['delivery_date', 'cee_id'])['assignment_to_Cee_time'].min().reset_index()
    cee_day.rename(columns={'assignment_to_Cee_time': 'first_asg'}, inplace=True)
    cee_day['is_late_cee'] = cee_day['first_asg'].dt.time > REPORT_CUTOFF
    df_act = df_act.merge(cee_day, on=['delivery_date', 'cee_id'], how='left')

    # DC Late Store Check
    dc_check = df_act.groupby(['delivery_date', 'sa_name'])['order_binned_time'].apply(lambda x: (x.dt.time > PICK_LIMIT).mean() > 0.5).reset_index()
    dc_check.rename(columns={'order_binned_time': 'is_dc_late'}, inplace=True)
    df_act = df_act.merge(dc_check, on=['delivery_date', 'sa_name'], how='left')

    # Late Flags
    df_act['Late'] = df_act['order_delivered_time'].apply(lambda x: x.time() > OTD_LIMIT if pd.notnull(x) else False)
    
    # --- 4. ROOT CAUSE ANALYSIS ENGINE ---
    def get_rca(row):
        if not row['Late']: return "On-time"
        no_rt = pd.isnull(row['route_id']) or row['route_id'] == 0
        if pd.notnull(row['order_binned_time']) and no_rt and row['Eff_Wait'] > 30: return "CEE Unavailable"
        if row.get('is_late_cee', False): return "CEE Late Reporting"
        if row['order_binned_time'].time() > PICK_LIMIT and not row['is_dc_late']: return "GRN / Picking Delay"
        if row['is_dc_late']: return "DC Arrival Issue"
        if row['Travel_Mins'] > 120: return "CEE Took More Time"
        return "Last Mile / Traffic"

    df_act['Primary_RCA'] = df_act.apply(get_rca, axis=1)

    # --- 5. TABS & DASHBOARD ---
    t_rca, t_city, t_rt, t_cee, t_soc, t_od = st.tabs(["RCA Summary", "City Summary", "Route Analysis", "CEE Performance", "Society Load", "Order Detail"])

    with t_rca: # Executive Impact Table
        st.subheader("📢 Executive Operational Impact Summary")
        impact = df_act[df_act['Late']].groupby(['city_name', 'Primary_RCA']).agg(Orders=('order_id', 'count'), Routes=('route_id', 'nunique'), Stores=('sa_name', 'nunique')).reset_index()
        st.table(impact.sort_values(['city_name', 'Orders'], ascending=[True, False]))

    with t_city: # Status Distribution
        st.subheader("City Status Pivot")
        city_pivot = df_f.pivot_table(index='city_name', columns='Live_Status', values='order_id', aggfunc='count', fill_value=0).reset_index()
        st.dataframe(city_pivot, width='stretch')

    with t_rt: # Route Productivity
        rt_view = df_act.groupby(['route_id', 'sa_name']).agg(Orders=('order_id', 'count'), Societies=('society_id', 'nunique'), Travel=('Travel_Mins', 'mean')).reset_index()
        c1, c2 = st.columns(2)
        c1.metric("Avg Orders/Route", f"{int(rt_view['Orders'].mean())}")
        c2.metric("Avg Societies/Route", f"{rt_view['Societies'].mean():.1f}")
        st.dataframe(rt_view.sort_values('Orders', ascending=False), width='stretch')

    with t_cee: # CEE Performance
        cee_view = df_act.groupby(['sa_name', 'cee_name']).agg(Trips=('route_id', 'nunique'), Orders=('order_id', 'count'), First_Asg=('first_asg', 'min')).reset_index()
        cee_view['First Start'] = cee_view['First_Asg'].dt.strftime('%H:%M')
        st.dataframe(cee_view[['cee_name', 'sa_name', 'Trips', 'Orders', 'First Start']].sort_values('Trips', ascending=False), width='stretch')

    with t_soc: # Society Analysis
        soc_v = df_act.groupby(['society_id', 'sa_name']).agg(Total_Orders=('order_id', 'count'), Impacted=('Late', 'sum'), CEEs=('cee_id', 'nunique')).reset_index()
        soc_v['Impact %'] = (soc_v['Impacted'] / soc_v['Total_Orders'] * 100).round(1)
        st.dataframe(soc_v.sort_values('Total_Orders', ascending=False), width='stretch')

    with t_od: # Detail Log
        st.dataframe(df_act[['order_id', 'sa_name', 'cee_name', 'Primary_RCA', 'Live_Status']], width='stretch')

else:
    st.info("Upload the CSV file in the sidebar to generate the report.")
