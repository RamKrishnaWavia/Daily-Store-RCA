import streamlit as st
import pandas as pd
import datetime

# Set Page Config
st.set_page_config(page_title="NOI Command Center", layout="wide")

st.title("🚚 Daily Delivery RCA Dashboard")
st.markdown("Upload your daily Express Order Report to analyze performance and root causes.")

# 1. Sidebar - File Upload & Date Filter
st.sidebar.header("Data Input & Filters")
uploaded_file = st.sidebar.file_uploader("Upload CSV", type="csv")

if uploaded_file:
    df = pd.read_csv(uploaded_file)
    
    # Pre-processing Timestamps
    time_cols = ['slot_from_time', 'order_binned_time', 'assignment_to_Cee_time', 'dispatch_time', 'order_delivered_time', 'order_in_process_time']
    for col in time_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce')
        else:
            df[col] = pd.NaT

    # --- FILTERS ---
    df['delivery_date'] = df['slot_from_time'].dt.date
    min_date = df['delivery_date'].dropna().min()
    max_date = df['delivery_date'].dropna().max()
    
    st.sidebar.subheader("Select Date Range")
    start_date = st.sidebar.date_input("From Date", min_date)
    end_date = st.sidebar.date_input("To Date", max_date)
    
    cities = sorted(df['city_name'].dropna().unique())
    selected_city = st.sidebar.selectbox("Select City", ["All Cities"] + list(cities))

    # Apply Filters
    df_filtered = df[(df['delivery_date'] >= start_date) & (df['delivery_date'] <= end_date)].copy()
    if selected_city != "All Cities":
        df_filtered = df_filtered[df_filtered['city_name'] == selected_city]
    
    if df_filtered.empty:
        st.warning("No data available for the selected filters.")
        st.stop()

    # --- UPDATED: STATUS CLASSIFIER ---
    def classify_status(row):
        status = str(row['order_status']).lower()
        if status in ['cancelled', 'payment_pending']: 
            return 'Cancelled'
        if status in ['complete', 'delivered', 'reached']: 
            return 'Delivered'
        if status in ['ready_to_ship', 'ofd', 'dispatched']: 
            return 'OFD'
        if status in ['binned', 'packed']: 
            return 'Bin'
        return 'Open'

    df_filtered['Live_Status'] = df_filtered.apply(classify_status, axis=1)

    # --- CORE LOGIC CALCULATIONS ---
    # Actionable dataset (excluding all types of cancellations per new rules)
    df_act = df_filtered[df_filtered['Live_Status'] != 'Cancelled'].copy()
    
    # Constants
    OTD_LIMIT = datetime.time(7, 0)
    PICK_LIMIT = datetime.time(4, 0)

    # 1. Wait Start Logic: Clock starts at 4 AM or Bin Time (whichever is later)
    df_act['four_am'] = pd.to_datetime(df_act['delivery_date'].astype(str) + ' 04:00:00')
    df_act['wait_start'] = df_act[['order_binned_time', 'four_am']].max(axis=1)
    df_act['Effective_Wait_Mins'] = (df_act['assignment_to_Cee_time'] - df_act['wait_start']).dt.total_seconds() / 60
    
    # 2. Travel Duration
    df_act['Travel_Mins'] = (df_act['order_delivered_time'] - df_act['assignment_to_Cee_time']).dt.total_seconds() / 60

    # 3. Wave Picking / DC Arrival Logic
    store_bin_stats = df_act.groupby('sa_name')['order_binned_time'].apply(
        lambda x: (x.dt.time > PICK_LIMIT).mean() > 0.50
    ).reset_index().rename(columns={'order_binned_time': 'is_dc_late_store'})
    df_act = df_act.merge(store_bin_stats, on='sa_name', how='left')

    # 4. Late & On-Time Flags
    df_act['Late'] = df_act['order_delivered_time'].apply(lambda x: x.time() > OTD_LIMIT if pd.notnull(x) else False)
    df_act['On_Time'] = (df_act['Live_Status'] == 'Delivered') & (~df_act['Late'])

    # 5. PRIMARY RCA LOGIC
    def calculate_rca(row):
        no_route = pd.isnull(row['route_id']) or row['route_id'] == 0
        if pd.notnull(row['order_binned_time']) and no_route and row['Effective_Wait_Mins'] > 30:
            return "CEE Unavailable"
        if pd.notnull(row['assignment_to_Cee_time']) and row['assignment_to_Cee_time'].time() > datetime.time(4, 30):
            return "CEE Late Reporting"
        delayed_picking_flag = str(row.get('Delayed_picking', 'no')).lower() == 'yes'
        if row['order_binned_time'].time() > PICK_LIMIT:
            if not row['is_dc_late_store'] or delayed_picking_flag:
                return "GRN / Picking Delay"
        if row['is_dc_late_store']:
            return "DC Arrival Issue"
        if row['Travel_Mins'] > 120:
            return "CEE Took More Time"
        return "Last Mile / Traffic"

    df_act['Primary_RCA'] = df_act.apply(lambda r: calculate_rca(r) if r['Late'] else "On-time", axis=1)

    # --- DASHBOARD HEADER ---
    st.markdown(f"### 📊 Operational Overview: {selected_city}")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Actionable Orders", f"{len(df_act):,}")
    c2.metric("On-Time Rate", f"{(df_act['On_Time'].mean()*100):.1f}%")
    c3.metric("On-Time (Before 7AM)", f"{df_act['On_Time'].sum():,}")
    c4.metric("Total Routes", f"{df_filtered['route_id'].nunique():,}")
    c5.metric("Total CEEs", f"{df_filtered['cee_id'].nunique():,}")
    c6.metric("Total Societies", f"{df_filtered['society_id'].nunique():,}")
    st.divider()

    # --- TABS ---
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "City Summary", "Delivery Slabs", "Store RCA", "Route Analysis", "CEE Performance", "Society Analysis", "Order Detail"
    ])

    with tab1: # City Summary
        city_summary = df_act.groupby('city_name').agg(Orders=('order_id', 'count'), On_Time=('On_Time', 'sum'), Late=('Late', 'sum'), Avg_Bin_Wait=('Effective_Wait_Mins', 'mean')).reset_index()
        status_pivot = df_filtered.pivot_table(index='city_name', columns='Live_Status', values='order_id', aggfunc='count', fill_value=0).reset_index()
        for col in ['Open', 'Bin', 'OFD', 'Delivered', 'Cancelled']:
            if col not in status_pivot.columns: status_pivot[col] = 0
        city_final = city_summary.merge(status_pivot, on='city_name', how='left')
        city_final['Late %'] = (city_final['Late'] / city_final['Orders'] * 100).round(1)
        city_final['Avg Bin Wait'] = city_final['Avg_Bin_Wait'].apply(lambda x: f"{int(x)}m" if x > 0 else "-")
        st.dataframe(city_final[['city_name', 'Orders', 'Open', 'Bin', 'OFD', 'Delivered', 'Cancelled', 'On_Time', 'Late', 'Late %', 'Avg Bin Wait']], width='stretch')
        st.bar_chart(df_act[df_act['Late'] == True]['Primary_RCA'].value_counts())

    with tab2: # Delivery Slabs
        def get_slab(t):
            if pd.isnull(t): return "Undelivered"
            t = t.time()
            if t <= datetime.time(7, 0): return "1. Before 7:00 AM"
            elif t <= datetime.time(7, 30): return "2. 7:00 - 7:30 AM"
            elif t <= datetime.time(8, 0): return "3. 7:30 - 8:00 AM"
            elif t <= datetime.time(8, 30): return "4. 8:00 - 8:30 AM"
            elif t <= datetime.time(9, 0): return "5. 8:30 - 9:00 AM"
            else: return "6. Post 9:00 AM"
        df_act['Slab'] = df_act['order_delivered_time'].apply(get_slab)
        slab_view = df_act[df_act['Slab'] != "Undelivered"].groupby('Slab').agg(Orders=('order_id', 'count'), Stores=('sa_name', 'nunique'), Societies=('society_id', 'nunique')).reset_index()
        st.dataframe(slab_view, width='stretch')

    with tab3: # STORE RCA (Updated with RCA Wise Summary)
        st.subheader("Store Performance & RCA Breakdown")
        store_base = df_act.groupby('sa_name').agg(
            Orders=('order_id', 'count'), On_Time=('On_Time', 'sum'), Late=('Late', 'sum'), Societies=('society_id', 'nunique'), Avg_Wait=('Effective_Wait_Mins', 'mean')
        ).reset_index()

        rca_pivot = df_act[df_act['Late'] == True].pivot_table(
            index='sa_name', columns='Primary_RCA', values='order_id', aggfunc='count', fill_value=0
        ).reset_index()

        store_final = store_base.merge(rca_pivot, on='sa_name', how='left').fillna(0)
        store_final['Late %'] = (store_final['Late'] / store_final['Orders'] * 100).round(1)
        store_final['Avg Bin Wait'] = store_final['Avg_Wait'].apply(lambda x: f"{int(x)}m" if x > 0 else "-")
        
        # Build dynamic column list
        base_cols = ['sa_name', 'Orders', 'On_Time', 'Late', 'Late %', 'Societies', 'Avg Bin Wait']
        rca_cols = [c for c in rca_pivot.columns if c != 'sa_name']
        st.dataframe(store_final[base_cols + rca_cols].sort_values('Late %', ascending=False), width='stretch')

    with tab4: # Route Analysis
        rt_view = df_act.groupby(['route_id', 'sa_name']).agg(Orders=('order_id', 'count'), Late=('Late', 'sum'), Societies=('society_id', 'nunique'), Avg_Wait=('Effective_Wait_Mins', 'mean'), Travel=('Travel_Mins', 'mean'), Route_Weight=('order_weight', 'sum' if 'order_weight' in df_act.columns else 'count')).reset_index()
        st.markdown("**Route Productivity Stats**")
        rs1, rs2 = st.columns(2); rs3, rs4 = st.columns(2)
        rs1.write(f"📏 **Orders per Route:** Min: {int(rt_view['Orders'].min())} | Max: {int(rt_view['Orders'].max())} | Avg: {int(rt_view['Orders'].mean())}")
        rs2.write(f"🏘️ **Societies per Route:** Min: {int(rt_view['Societies'].min())} | Max: {int(rt_view['Societies'].max())} | Avg: {rt_view['Societies'].mean():.1f}")
        rs3.write(f"⏱️ **Avg Travel (Assign $\\rightarrow$ Deliv):** {int(rt_view['Travel'].mean())}m")
        if 'order_weight' in df_act.columns: rs4.write(f"⚖️ **Weight per Route:** Min: {rt_view['Route_Weight'].min():.1f}kg | Max: {rt_view['Route_Weight'].max():.1f}kg | Avg: {rt_view['Route_Weight'].mean():.1f}kg")
        st.divider()
        rt_view['Late Rate'] = (rt_view['Late'] / rt_view['Orders'] * 100).round(0).astype(int).astype(str) + '%'
        rt_view['Avg Bin Wait'] = rt_view['Avg_Wait'].apply(lambda x: f"{int(x)}m" if x > 0 else "-")
        rt_view['Avg Travel'] = rt_view['Travel'].apply(lambda x: f"{int(x)}m" if x > 0 else "-")
        rt_view['Weight'] = rt_view['Route_Weight'].apply(lambda x: f"{x:.1f}kg")
        st.dataframe(rt_view[['route_id', 'sa_name', 'Orders', 'Weight', 'Late Rate', 'Societies', 'Avg Bin Wait', 'Avg Travel']].sort_values('Orders', ascending=False), width='stretch')

    with tab5: # CEE Performance
        cee_base = df_act.groupby(['cee_name', 'cee_id', 'sa_name']).agg(Trips=('route_id', 'nunique'), Orders=('order_id', 'count'), Societies=('society_id', 'nunique'), Late=('Late', 'sum'), First_Asg=('assignment_to_Cee_time', 'min')).reset_index()
        trip_dist = cee_base['Trips'].value_counts().sort_index().reset_index()
        trip_dist.columns = ['Trips Completed', 'No. of CEEs']
        col_dist1, col_dist2 = st.columns([1, 2])
        with col_dist1: st.dataframe(trip_dist, width='stretch')
        with col_dist2: st.bar_chart(trip_dist.set_index('Trips Completed'))
        st.divider()
        def get_cee_slab(t):
            if pd.isnull(t): return "-"
            tm = t.time()
            if tm >= datetime.time(6, 0): return "> 06:00 AM"
            elif tm >= datetime.time(5, 30): return "> 05:30 AM"
            elif tm >= datetime.time(5, 0): return "> 05:00 AM"
            elif tm >= datetime.time(4, 30): return "> 04:30 AM"
            return "On-Time Start"
        cee_base['Reporting Slab'] = cee_base['First_Asg'].apply(get_cee_slab)
        cee_base['Late Rate %'] = (cee_base['Late'] / cee_base['Orders'] * 100).round(1)
        st.dataframe(cee_base[['cee_name', 'sa_name', 'Trips', 'Orders', 'Societies', 'Reporting Slab', 'Late Rate %']].sort_values('Trips', ascending=False), width='stretch')

    with tab6: # Society Analysis
        soc_view = df_act.groupby(['society_id', 'sa_name']).agg(Orders=('order_id', 'count'), Routes=('route_id', 'nunique'), CEEs=('cee_id', 'nunique'), Late=('Late', 'sum')).reset_index()
        st.dataframe(soc_view.sort_values('Orders', ascending=False), width='stretch')

    with tab7: # Order Detail
        od = df_act.copy()
        od['Bin_Time'] = od['order_binned_time'].dt.strftime('%H:%M')
        od['Asg_Time'] = od['assignment_to_Cee_time'].dt.strftime('%H:%M')
        od['Deliv_Time'] = od['order_delivered_time'].dt.strftime('%H:%M')
        od_cols = ['order_id', 'sa_name', 'cee_name', 'Bin_Time', 'Asg_Time', 'Deliv_Time', 'Primary_RCA']
        st.dataframe(od[od_cols], width='stretch')

else:
    st.info("Please upload the Delivery Report CSV to begin.")
