import streamlit as st
import pandas as pd
import datetime

# --- 1. INITIAL SETUP ---
st.set_page_config(page_title="Personal Command Center", layout="wide")
st.title("🚚 Daily Delivery & RCA Command Center")

uploaded_file = st.sidebar.file_uploader("Upload Delivery Report (CSV)", type="csv")

if uploaded_file:
    # --- LOAD DATA ---
    df = pd.read_csv(uploaded_file)
    
    # --- ROBUST DATE PARSING ---
    time_cols = ['slot_from_time', 'order_binned_time', 'assignment_to_Cee_time', 'order_delivered_time']
    for col in time_cols:
        if col in df.columns:
            # dayfirst=True ensures DD-MM-YYYY format is correctly prioritized
            df[col] = pd.to_datetime(df[col], dayfirst=True, errors='coerce')

    # Extract clean date for the filters
    df['delivery_date'] = df['slot_from_time'].dt.date
    available_dates = sorted(df['delivery_date'].dropna().unique())
    
    if not available_dates:
        st.error("No valid dates found in 'slot_from_time'. Check your file format.")
        st.stop()
        
    f_min, f_max = available_dates[0], available_dates[-1]

    # --- 2. GLOBAL FILTERS ---
    st.sidebar.subheader("📅 Data Filters")
    start_dt = st.sidebar.date_input("Start Date", f_min)
    end_dt = st.sidebar.date_input("End Date", f_max)
    
    cities = sorted(df['city_name'].dropna().unique().tolist())
    sel_city = st.sidebar.selectbox("Select City", ["All Cities"] + cities)

    mask = (df['delivery_date'] >= start_dt) & (df['delivery_date'] <= end_dt)
    df_f = df[mask].copy()
    if sel_city != "All Cities":
        df_f = df_f[df_f['city_name'] == sel_city]

    if df_f.empty:
        st.warning(f"No records found for the selected criteria.")
        st.stop()

    # --- 3. STATUS & OPERATIONAL LOGIC ---
    def get_live_status(row):
        s = str(row['order_status']).strip().lower()
        if s in ['cancelled', 'payment_pending']: return 'Cancelled'
        if s in ['complete', 'delivered']: return 'Delivered'
        if s in ['reached']: return 'Reached'
        if s in ['ready_to_ship', 'ofd', 'dispatched']: return 'OFD'
        return 'Bin'

    df_f['Live_Status'] = df_f.apply(get_live_status, axis=1)
    # Actionable dataset: Excludes Cancelled/Payment Pending
    df_act = df_f[df_f['Live_Status'] != 'Cancelled'].copy()

    # Constants & Timing
    OTD_LIMIT, PICK_LIMIT = datetime.time(7, 0), datetime.time(4, 0)

    df_act['four_am'] = pd.to_datetime(df_act['delivery_date'].astype(str) + ' 04:00:00')
    df_act['wait_start'] = df_act[['order_binned_time', 'four_am']].max(axis=1)
    
    # Eff_Wait: Time between Assignment and (Max of 4 AM or Binning)
    df_act['Eff_Wait'] = (df_act['assignment_to_Cee_time'] - df_act['wait_start']).dt.total_seconds() / 60
    # Travel_Mins: Time from Assignment to Delivery
    df_act['Travel_Mins'] = (df_act['order_delivered_time'] - df_act['assignment_to_Cee_time']).dt.total_seconds() / 60

    # DC Arrival Logic (Store-wide Check: >50% binned post 4 AM)
    dc_check = df_act.groupby(['delivery_date', 'sa_name'])['order_binned_time'].apply(
        lambda x: (x.dt.time > PICK_LIMIT).mean() > 0.5
    ).reset_index().rename(columns={'order_binned_time': 'is_dc_late'})
    df_act = df_act.merge(dc_check, on=['delivery_date', 'sa_name'], how='left')

    # Flag SLA Breach (> 7 AM)
    df_act['SLA_Breach'] = df_act['order_delivered_time'].apply(lambda x: x.time() > OTD_LIMIT if pd.notnull(x) else False)
    
    # --- 4. REFINED RCA ENGINE ---
    def get_rca(row):
        if not row['SLA_Breach']: return "On-time"
        
        # 1. CEE Unavailable: Binned but no assignment time recorded
        if pd.notnull(row['order_binned_time']) and pd.isnull(row['assignment_to_Cee_time']):
            return "CEE Unavailable"
        
        # 2. CEE Late Reporting: (Asg Time - max(Bin, 4 AM)) > 30 minutes
        if row['Eff_Wait'] > 30:
            return "CEE Late Reporting"
        
        # 3. DC Arrival Issue: Late supply from DC to store (>50% late binning)
        if row['is_dc_late']:
            return "DC Arrival Issue"
        
        # 4. GRN / Picking Delay: Specific order binned after 4 AM (internal store delay)
        if row['order_binned_time'].time() > PICK_LIMIT:
            return "GRN / Picking Delay"
        
        # 5. CEE Took More Time: Travel/Delivery took more than 2 hours
        if row['Travel_Mins'] > 120:
            return "CEE Took More Time"
            
        return "Operational Delay"

    df_act['Primary_RCA'] = df_act.apply(get_rca, axis=1)

    # --- 5. DASHBOARD TABS ---
    t_sla, t_city, t_rt, t_cee, t_soc, t_od = st.tabs([
        "OTD 7 AM SLA Breached RCA", "City Summary", "Route Analysis", "CEE Performance", "Society Load", "Audit Log"
    ])

    with t_sla: # NEW BREACH DIVE-IN TAB
        st.subheader("📉 OTD 7 AM SLA Breach Analysis")
        breached_df = df_act[df_act['SLA_Breach'] == True]
        
        # Summary Metrics
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Breached Orders", f"{len(breached_df):,}")
        m2.metric("SLA Success Rate", f"{( (1 - len(breached_df)/len(df_act)) * 100):.1f}%")
        m3.metric("Stores with Breaches", breached_df['sa_name'].nunique())
        
        st.divider()
        
        # Impact Table
        st.markdown("#### Root Cause Contribution (Impact Table)")
        impact = breached_df.groupby(['city_name', 'Primary_RCA']).agg(
            Orders_Impacted=('order_id', 'count'),
            Routes_Impacted=('route_id', 'nunique'),
            Stores_Impacted=('sa_name', 'nunique')
        ).reset_index()
        st.table(impact.sort_values(['city_name', 'Orders_Impacted'], ascending=[True, False]))
        
        st.divider()
        
        # Store Drill-down
        st.markdown("#### Store-wise RCA Breakdown")
        store_pivot = breached_df.pivot_table(index='sa_name', columns='Primary_RCA', values='order_id', aggfunc='count', fill_value=0)
        st.dataframe(store_pivot, width='stretch')

    with t_city:
        st.subheader("City Status Pivot")
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
        st.subheader("Society Load vs. Impact")
        soc_v = df_act.groupby(['society_id', 'sa_name']).agg(Total_Orders=('order_id', 'count'), Impacted_Orders=('SLA_Breach', 'sum')).reset_index()
        so1, so2 = st.columns(2)
        so1.metric("Avg Orders/Society", int(soc_v['Total_Orders'].mean()))
        so2.metric("Impact Rate (%)", f"{(soc_v['Impacted_Orders'].sum()/soc_v['Total_Orders'].sum() * 100):.1f}%")
        st.dataframe(soc_v.sort_values('Total_Orders', ascending=False), width='stretch')

    with t_od:
        st.subheader("Audit Detail Log")
        st.dataframe(df_act[['order_id', 'sa_name', 'cee_name', 'Primary_RCA', 'Live_Status']], width='stretch')

else:
    st.info("Please upload the CSV to begin. The dashboard will automatically filter for April 19th if that is the date in your file.")
