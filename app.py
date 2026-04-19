import streamlit as st
import pandas as pd
import datetime

# --- 1. CONFIG & UI SETUP ---
st.set_page_config(page_title="NOI Command Center", layout="wide")
st.title("🚚 Daily Delivery RCA Dashboard")

uploaded_file = st.sidebar.file_uploader("Upload Express Order Report", type="csv")

if uploaded_file:
    df = pd.read_csv(uploaded_file)
    
    # Standardize Timestamps
    time_cols = ['slot_from_time', 'order_binned_time', 'assignment_to_Cee_time', 'order_delivered_time']
    for col in time_cols:
        df[col] = pd.to_datetime(df[col], errors='coerce')

    # Sidebar Filters
    df['delivery_date'] = df['slot_from_time'].dt.date
    selected_city = st.sidebar.selectbox("Select City", ["All Cities"] + sorted(df['city_name'].unique().tolist()))
    
    df_filtered = df.copy()
    if selected_city != "All Cities":
        df_filtered = df_filtered[df_filtered['city_name'] == selected_city]

    # --- 2. OPERATIONAL LOGIC & REFINEMENTS ---
    
    # Status Mapping (Reached is visible, payment_pending is Cancelled)
    def classify_status(row):
        status = str(row['order_status']).strip().lower()
        if status in ['cancelled', 'payment_pending']: return 'Cancelled'
        if status in ['complete', 'delivered']: return 'Delivered'
        if status in ['reached']: return 'Reached'
        if status in ['ready_to_ship', 'ofd', 'dispatched']: return 'OFD'
        return 'Bin'

    df_filtered['Live_Status'] = df_filtered.apply(classify_status, axis=1)
    df_act = df_filtered[df_filtered['Live_Status'] != 'Cancelled'].copy()

    # Constants
    OTD_LIMIT = datetime.time(7, 0)
    PICK_LIMIT = datetime.time(4, 0)
    REPORT_CUTOFF = datetime.time(4, 30)

    # Calculate Wait & Travel
    df_act['four_am'] = pd.to_datetime(df_act['delivery_date'].astype(str) + ' 04:00:00')
    df_act['wait_start'] = df_act[['order_binned_time', 'four_am']].max(axis=1)
    df_act['Eff_Wait'] = (df_act['assignment_to_Cee_time'] - df_act['wait_start']).dt.total_seconds() / 60
    df_act['Travel_Mins'] = (df_act['order_delivered_time'] - df_act['assignment_to_Cee_time']).dt.total_seconds() / 60

    # CEE First-Assignment Logic (For Late Reporting)
    cee_first = df_act.groupby(['delivery_date', 'cee_id'])['assignment_to_Cee_time'].min().reset_index()
    cee_first.rename(columns={'assignment_to_Cee_time': 'first_asg'}, inplace=True)
    cee_first['is_late_cee'] = cee_first['first_asg'].dt.time > REPORT_CUTOFF
    df_act = df_act.merge(cee_first, on=['delivery_date', 'cee_id'], how='left')

    # DC Arrival Logic (Store-wide delay check)
    store_bin = df_act.groupby(['delivery_date', 'sa_name'])['order_binned_time'].apply(lambda x: (x.dt.time > PICK_LIMIT).mean() > 0.5).reset_index()
    store_bin.rename(columns={'order_binned_time': 'is_dc_late'}, inplace=True)
    df_act = df_act.merge(store_bin, on=['delivery_date', 'sa_name'], how='left')

    # Late Flags
    df_act['Late'] = df_act['order_delivered_time'].apply(lambda x: x.time() > OTD_LIMIT if pd.notnull(x) else False)
    
    # --- 3. ROOT CAUSE ANALYSIS (RCA) ENGINE ---
    def calculate_rca(row):
        if not row['Late']: return "On-time"
        no_route = pd.isnull(row['route_id']) or row['route_id'] == 0
        if pd.notnull(row['order_binned_time']) and no_route and row['Eff_Wait'] > 30: return "CEE Unavailable"
        if row.get('is_late_cee', False): return "CEE Late Reporting"
        if row['order_binned_time'].time() > PICK_LIMIT and not row['is_dc_late']: return "GRN / Picking Delay"
        if row['is_dc_late']: return "DC Arrival Issue"
        if row['Travel_Mins'] > 120: return "CEE Took More Time"
        return "Last Mile / Traffic"

    df_act['Primary_RCA'] = df_act.apply(calculate_rca, axis=1)

    # --- 4. TABS & VISUALIZATION ---
    t_rca, t_city, t_cee, t_soc, t_od = st.tabs(["RCA Summary", "City Summary", "CEE Performance", "Society Load", "Order Detail"])

    with t_rca: # Executive Impact Table
        st.subheader("📢 Operational Impact Summary")
        impact = df_act[df_act['Late']].groupby(['city_name', 'Primary_RCA']).agg(Orders=('order_id', 'count'), Routes=('route_id', 'nunique'), Stores=('sa_name', 'nunique')).reset_index()
        st.table(impact.sort_values(['city_name', 'Orders'], ascending=[True, False]))

    with t_city: # Performance by City/Status
        status_pivot = df_filtered.pivot_table(index='city_name', columns='Live_Status', values='order_id', aggfunc='count', fill_value=0).reset_index()
        st.dataframe(status_pivot, width='stretch')

    with t_cee: # Multi-tripping & Efficiency
        cee_view = df_act.groupby(['sa_name', 'cee_name']).agg(Trips=('route_id', 'nunique'), Orders=('order_id', 'count'), Start=('first_asg', 'min')).reset_index()
        cee_view['Start'] = cee_view['Start'].dt.strftime('%H:%M')
        st.dataframe(cee_view.sort_values('Trips', ascending=False), width='stretch')

    with t_soc: # Society Load (Total vs Impacted)
        soc_view = df_act.groupby(['society_id', 'sa_name']).agg(Total_Orders=('order_id', 'count'), Impacted_Orders=('Late', 'sum'), CEEs=('cee_id', 'nunique')).reset_index()
        soc_view['Impact %'] = (soc_view['Impacted_Orders'] / soc_view['Total_Orders'] * 100).round(1)
        st.dataframe(soc_view.sort_values('Total_Orders', ascending=False), width='stretch')

    with t_od: # Raw Audit Log
        st.dataframe(df_act[['order_id', 'sa_name', 'cee_name', 'Primary_RCA', 'Live_Status']], width='stretch')

else:
    st.info("Please upload a CSV file to begin.")
