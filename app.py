import streamlit as st
import pandas as pd
import datetime

# --- 1. INITIAL SETUP ---
st.set_page_config(page_title="Personal Command Center", layout="wide")
st.title("🚚 Daily Delivery & RCA Command Center")

# Updated type to allow both csv and xlsx
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
            temp_df = pd.read_csv(file)
        else:
            # For Excel files (.xlsx)
            temp_df = pd.read_excel(file)
        df_list.append(temp_df)
    
    df = pd.concat(df_list, ignore_index=True)
    
    # --- DATE PARSING (Strictly DD-MM-YYYY) ---
    time_cols = ['slot_from_time', 'order_binned_time', 'assignment_to_Cee_time', 'order_delivered_time']
    for col in time_cols:
        if col in df.columns:
            # dayfirst=True ensures April 19th (19-04) is read correctly
            df[col] = pd.to_datetime(df[col], dayfirst=True, errors='coerce')

    # Extract date for filters
    df['delivery_date'] = df['slot_from_time'].dt.date
    available_dates = sorted(df['delivery_date'].dropna().unique())
    
    if not available_dates:
        st.error("No valid dates found in data. Check 'slot_from_time' column.")
        st.stop()
        
    f_min, f_max = available_dates[0], available_dates[-1]

    # --- 2. FILTERS (Auto-synced to your file dates) ---
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
        st.warning(f"No records found for the selection.")
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

    # Core Calculations
    df_act['four_am'] = pd.to_datetime(df_act['delivery_date'].astype(str) + ' 04:00:00')
    df_act['wait_start'] = df_act[['order_binned_time', 'four_am']].max(axis=1)
    
    df_act['Eff_Wait'] = (df_act['assignment_to_Cee_time'] - df_act['wait_start']).dt.total_seconds() / 60
    df_act['Travel_Mins'] = (df_act['order_delivered_time'] - df_act['assignment_to_Cee_time']).dt.total_seconds() / 60

    # DC Arrival Logic (Store-wide Check)
    dc_check = df_act.groupby(['delivery_date', 'sa_name'])['order_binned_time'].apply(
        lambda x: (x.dt.time > PICK_LIMIT).mean() > 0.5
    ).reset_index().rename(columns={'order_binned_time': 'is_dc_late'})
    df_act = df_act.merge(dc_check, on=['delivery_date', 'sa_name'], how='left')

    # SLA Breach Flag (> 7 AM)
    df_act['SLA_Breach'] = df_act['order_delivered_time'].apply(lambda x: x.time() > OTD_LIMIT if pd.notnull(x) else False)
    
    # --- 4. RCA ENGINE ---
    def calculate_rca(row):
        if not row['SLA_Breach']: return "On-time"
        
        if pd.notnull(row['order_binned_time']) and pd.isnull(row['assignment_to_Cee_time']):
            return "CEE Unavailable"
        
        if row['Eff_Wait'] > 30:
            return "CEE Late Reporting"
        
        if row['is_dc_late']:
            return "DC Arrival Issue"
        
        if row['order_binned_time'].time() > PICK_LIMIT:
            return "GRN / Picking Delay"
        
        if row['Travel_Mins'] > 120:
            return "CEE Took More Time"
            
        return "Operational Delay"

    df_act['Primary_RCA'] = df_act.apply(calculate_rca, axis=1)

    # --- 5. TABS WITH FULL SUMMARIES ---
    t_sla, t_city, t_rt, t_cee, t_soc, t_od = st.tabs([
        "OTD 7 AM SLA Breached RCA", "City Summary", "Route Analysis", "CEE Performance", "Society Load", "Audit Log"
    ])

    with t_sla:
        st.subheader("📉 OTD 7 AM SLA Breach Analysis")
        breached = df_act[df_act['SLA_Breach']]
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
        ce1, ce2 = st.columns(2)
        ce1.metric("Active CEEs", len(cee_v))
        ce2.metric("Multi-Tripping Rate", f"{((cee_v['Trips']>1).mean()*100):.1f}%")
        st.dataframe(cee_v.sort_values('Trips', ascending=False), width='stretch')

    with t_soc:
        st.subheader("Society Load Analysis (All Orders)")
        soc_v = df_act.groupby(['society_id', 'sa_name']).agg(Total_Orders=('order_id', 'count'), Impacted_Orders=('SLA_Breach', 'sum')).reset_index()
        so1, so2 = st.columns(2)
        so1.metric("Avg Load / Society", int(soc_v['Total_Orders'].mean()))
        so2.metric("Total Societies Handled", len(soc_v))
        soc_v['Impact %'] = (soc_v['Impacted_Orders'] / soc_v['Total_Orders'] * 100).round(1)
        st.dataframe(soc_v.sort_values('Total_Orders', ascending=False), width='stretch')

    with t_od:
        st.subheader("Detailed Audit Log")
        st.write(f"🔍 Actionable Orders Processed: {len(df_act)}")
        st.dataframe(df_act[['order_id', 'sa_name', 'cee_name', 'Primary_RCA', 'Live_Status']], width='stretch')

else:
    st.info("Please upload your CSV or XLSX file(s) to begin.")
