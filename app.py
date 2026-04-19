import streamlit as st
import pandas as pd
import datetime

# Set Page Config
st.set_page_config(page_title="NOI Command Center", layout="wide")

st.title("🚚 Daily Delivery RCA Dashboard")
st.markdown("Upload your daily Express Order Report to analyze performance and root causes.")

# 1. Sidebar - File Upload & Global Filters
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

    # --- GLOBAL FILTERS ---
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

    # --- STATUS CLASSIFIER (Refined Operational Rules) ---
    def classify_status(row):
        status = str(row['order_status']).strip().lower()
        if status in ['cancelled', 'payment_pending']: 
            return 'Cancelled'
        if status in ['complete', 'delivered']: 
            return 'Delivered'
        if status in ['reached']: 
            return 'Reached'
        if status in ['ready_to_ship', 'ofd', 'dispatched']: 
            return 'OFD'
        if status in ['binned', 'packed']: 
            return 'Bin'
        return 'Open'

    df_filtered['Live_Status'] = df_filtered.apply(classify_status, axis=1)

    # --- CORE LOGIC CALCULATIONS ---
    # Actionable dataset excludes all Cancelled (including payment_pending)
    df_act = df_filtered[df_filtered['Live_Status'] != 'Cancelled'].copy()
    
    OTD_LIMIT = datetime.time(7, 0)
    PICK_LIMIT = datetime.time(4, 0)

    # 1. Effective Wait Logic: Clock starts at 4 AM or Bin Time
    df_act['four_am'] = pd.to_datetime(df_act['delivery_date'].astype(str) + ' 04:00:00')
    df_act['wait_start'] = df_act[['order_binned_time', 'four_am']].max(axis=1)
    df_act['Effective_Wait_Mins'] = (df_act['assignment_to_Cee_time'] - df_act['wait_start']).dt.total_seconds() / 60
    
    # 2. Travel Duration (Assignment to Delivery)
    df_act['Travel_Mins'] = (df_act['order_delivered_time'] - df_act['assignment_to_Cee_time']).dt.total_seconds() / 60

    # 3. Wave Picking / DC Arrival Logic (Store-wide Check)
    store_bin_stats = df_act.groupby('sa_name')['order_binned_time'].apply(
        lambda x: (x.dt.time > PICK_LIMIT).mean() > 0.50
    ).reset_index().rename(columns={'order_binned_time': 'is_dc_late_store'})
    df_act = df_act.merge(store_bin_stats, on='sa_name', how='left')

    # 4. Late & On-Time Flags
    df_act['Late'] = df_act['order_delivered_time'].apply(lambda x: x.time() > OTD_LIMIT if pd.notnull(x) else False)
    # Reached orders are treated as "Delivered" for performance tracking
    df_act['On_Time'] = (df_act['Live_Status'].isin(['Delivered', 'Reached'])) & (~df_act['Late'])

    # 5. PRIMARY RCA LOGIC
    def calculate_rca(row):
        if not row['Late']: return "On-time"
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

    # ========================================================
    # EXECUTIVE IMPACT SUMMARY TABLE
    # ========================================================
    st.markdown("### 📢 Executive Impact Summary (Late Deliveries)")
    impact_data = df_act[df_act['Late'] == True].groupby(['city_name', 'Primary_RCA']).agg(
        Orders_Impacted=('order_id', 'count'),
        Routes_Impacted=('route_id', 'nunique'),
        Stores_Affected=('sa_name', 'nunique')
    ).reset_index()

    if not impact_data.empty:
        impact_data = impact_data.sort_values(['city_name', 'Orders_Impacted'], ascending=[True, False])
        impact_data.columns = ['City', 'Root Cause (RCA)', 'Orders Impacted', 'Routes Impacted', 'Stores Affected']
        st.table(impact_data)
    else:
        st.success("✅ No operational impacts recorded (100% On-Time Performance).")

    st.divider()

    # --- MAIN DASHBOARD HEADER ---
    st.markdown(f"### 📊 Operational Overview: {selected_city}")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Actionable Orders", f"{len(df_act):,}")
    c2.metric("On-Time Rate", f"{(df_act['On_Time'].mean()*100):.1f}%")
    c3.metric("On-Time (Before 7AM)", f"{df_act['On_Time'].sum():,}")
    c4.metric("Total Routes", f"{df_filtered['route_id'].nunique():,}")
    c5.metric("Total CEEs", f"{df_filtered['cee_id'].nunique():,}")
    c6.metric("Total Societies", f"{df_filtered['society_id'].nunique():,}")
    st.divider()

    # --- TABS NAVIGATION ---
    tab0, tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "RCA Summary", "City Summary", "Delivery Slabs", "Store RCA", "Route Analysis", "CEE Performance", "Society Analysis", "Order Detail"
    ])

    # --- TAB 0: RCA SUMMARY ---
    with tab0:
        st.subheader("RCA Breakdown & Contribution")
        late_only = df_act[df_act['Late'] == True]
        if not late_only.empty:
            rca_counts = late_only['Primary_RCA'].value_counts().reset_index()
            rca_counts.columns = ['Root Cause', 'Order_Count']
            rca_counts['Contribution %'] = (rca_counts['Order_Count'] / rca_counts['Order_Count'].sum() * 100).round(1).astype(str) + '%'
            
            sc1, sc2 = st.columns([1, 1])
            with sc1: st.dataframe(rca_counts, width='stretch')
            with sc2: st.bar_chart(rca_counts.set_index('Root Cause')['Order_Count'])
        else:
            st.success("No late orders to analyze.")

    # --- TAB 1: CITY SUMMARY ---
    with tab1:
        st.subheader("City Productivity & Status Summary")
        city_sum_base = df_act.groupby('city_name').agg(Orders=('order_id', 'count'), On_Time=('On_Time', 'sum'), Late=('Late', 'sum'), Avg_Wait=('Effective_Wait_Mins', 'mean')).reset_index()
        city_sum_base['Late %'] = (city_sum_base['Late'] / city_sum_base['Orders'] * 100).round(1)
        
        # Stats Row
        cs1, cs2 = st.columns(2)
        cs1.write(f"📈 **Late %:** Min: {city_sum_base['Late %'].min()}% | Max: {city_sum_base['Late %'].max()}% | Avg: {city_sum_base['Late %'].mean():.1f}%")
        cs2.write(f"⏱️ **Wait Time:** Min: {int(city_sum_base['Avg_Wait'].min())}m | Max: {int(city_sum_base['Avg_Wait'].max())}m | Avg: {int(city_sum_base['Avg_Wait'].mean())}m")
        
        status_pivot = df_filtered.pivot_table(index='city_name', columns='Live_Status', values='order_id', aggfunc='count', fill_value=0).reset_index()
        for col in ['Open', 'Bin', 'OFD', 'Reached', 'Delivered', 'Cancelled']:
            if col not in status_pivot.columns: status_pivot[col] = 0
            
        city_final = city_sum_base.merge(status_pivot, on='city_name', how='left')
        city_final['Avg Bin Wait'] = city_final['Avg_Wait'].apply(lambda x: f"{int(x)}m" if x > 0 else "-")
        st.dataframe(city_final[['city_name', 'Orders', 'Open', 'Bin', 'OFD', 'Reached', 'Delivered', 'Cancelled', 'On_Time', 'Late %', 'Avg Bin Wait']].sort_values('Late %', ascending=False), width='stretch')

    # --- TAB 2: DELIVERY SLABS ---
    with tab2:
        st.subheader("Delivery Timing Breakdown")
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
        
        st.markdown(f"🚀 **Peak Delivery Volume Slab:** {slab_view.loc[slab_view['Orders'].idxmax(), 'Slab']}")
        st.dataframe(slab_view, width='stretch')
        st.bar_chart(slab_view.set_index('Slab')['Orders'])

    # --- TAB 3: STORE RCA ---
    with tab3:
        st.subheader("Store-Wise Performance & RCA Pivot")
        store_base = df_act.groupby('sa_name').agg(Orders=('order_id', 'count'), On_Time=('On_Time', 'sum'), Late=('Late', 'sum'), Societies=('society_id', 'nunique'), Avg_Wait=('Effective_Wait_Mins', 'mean')).reset_index()
        store_base['Late %'] = (store_base['Late'] / store_base['Orders'] * 100).round(1)
        
        sts1, sts2 = st.columns(2)
        sts1.write(f"🏘️ **Societies/Store:** Avg: {int(store_base['Societies'].mean())}")
        sts2.write(f"⏱️ **Wait Time:** Avg: {int(store_base['Avg_Wait'].mean())}m")

        rca_pivot = df_act[df_act['Late'] == True].pivot_table(index='sa_name', columns='Primary_RCA', values='order_id', aggfunc='count', fill_value=0).reset_index()
        store_final = store_base.merge(rca_pivot, on='sa_name', how='left').fillna(0)
        st.dataframe(store_final.sort_values('Late %', ascending=False), width='stretch')

    # --- TAB 4: ROUTE ANALYSIS ---
    with tab4:
        st.subheader("Route Productivity Stats")
        rt_view = df_act.groupby(['route_id', 'sa_name']).agg(
            Orders=('order_id', 'count'), Late=('Late', 'sum'), Societies=('society_id', 'nunique'), Travel=('Travel_Mins', 'mean'), 
            Weight=('order_weight', 'sum' if 'order_weight' in df_act.columns else 'count')
        ).reset_index()
        
        rs1, rs2, rs3 = st.columns(3)
        rs1.write(f"📏 **Orders/Route:** Avg: {int(rt_view['Orders'].mean())}")
        # KEY REQ: Avg Societies in one route
        rs2.write(f"🏘️ **Societies/Route:** Min: {int(rt_view['Societies'].min())} | Max: {int(rt_view['Societies'].max())} | **Avg: {rt_view['Societies'].mean():.1f}**")
        rs3.write(f"⏱️ **Travel Time:** Avg: {int(rt_view['Travel'].mean())}m")
        
        st.divider()
        rt_view['Late Rate'] = (rt_view['Late'] / rt_view['Orders'] * 100).round(0).astype(int).astype(str) + '%'
        rt_view['Avg Travel'] = rt_view['Travel'].apply(lambda x: f"{int(x)}m" if pd.notnull(x) else "-")
        st.dataframe(rt_view[['route_id', 'sa_name', 'Orders', 'Late Rate', 'Societies', 'Avg Travel']].sort_values('Orders', ascending=False), width='stretch')

    # --- TAB 5: CEE PERFORMANCE ---
    with tab5:
        st.subheader("CEE Efficiency & Timing Audit")
        cee_base = df_act.groupby(['city_name', 'sa_name', 'cee_name', 'cee_id']).agg(
            Trips=('route_id', 'nunique'), Orders=('order_id', 'count'), Societies=('society_id', 'nunique'), Late=('Late', 'sum'),
            First_Asg=('assignment_to_Cee_time', 'min'), Last_Asg=('assignment_to_Cee_time', 'max'),
            First_Del=('order_delivered_time', 'min'), Last_Del=('order_delivered_time', 'max')
        ).reset_index()

        eff1, eff2, eff3 = st.columns(3)
        eff1.metric("Multi-Tripping %", f"{((cee_base['Trips'] > 1).mean()*100):.1f}%")
        eff2.metric("Avg Orders/CEE", f"{int(cee_base['Orders'].mean())}")
        eff3.metric("Avg Societies/CEE", f"{int(cee_base['Societies'].mean())}")

        st.divider()
        # Reporting Slab Table
        def get_cee_slab(t):
            if pd.isnull(t): return "-"
            tm = t.time()
            if tm >= datetime.time(6, 0): return "> 06:00 AM"
            elif tm >= datetime.time(5, 30): return "> 05:30 AM"
            elif tm >= datetime.time(5, 0): return "> 05:00 AM"
            elif tm >= datetime.time(4, 30): return "> 04:30 AM"
            return "On-Time Start"
        
        cee_base['Reporting Slab'] = cee_base['First_Asg'].apply(get_cee_slab)
        slab_mode = st.radio("Slab Summary Grouping:", ["City", "Store"], horizontal=True)
        slab_pivot = cee_base.pivot_table(index='city_name' if slab_mode == "City" else 'sa_name', columns='Reporting Slab', values='cee_id', aggfunc='nunique', fill_value=0).reset_index()
        st.dataframe(slab_pivot, width='stretch')

        st.divider()
        cee_base['First assignment'] = cee_base['First_Asg'].dt.strftime('%H:%M')
        cee_base['Last assignment'] = cee_base['Last_Asg'].dt.strftime('%H:%M')
        cee_base['First delivery'] = cee_base['First_Del'].dt.strftime('%H:%M')
        cee_base['Last delivery'] = cee_base['Last_Del'].dt.strftime('%H:%M')
        cee_base['Late Rate %'] = (cee_base['Late'] / cee_base['Orders'] * 100).round(1)
        st.dataframe(cee_base[['cee_name', 'sa_name', 'Trips', 'Orders', 'Societies', 'First assignment', 'Last assignment', 'First delivery', 'Last delivery', 'Reporting Slab', 'Late Rate %']].sort_values('Trips', ascending=False), width='stretch')

    # --- TAB 6: SOCIETY ANALYSIS ---
    with tab6:
        st.subheader("Society Load & Fragmentation Stats")
        soc_view = df_act.groupby(['society_id', 'sa_name']).agg(Orders=('order_id', 'count'), Routes=('route_id', 'nunique'), CEEs=('cee_id', 'nunique')).reset_index()
        sos1, sos2 = st.columns(2)
        sos1.write(f"📦 **Orders/Society:** Avg: {int(soc_view['Orders'].mean())}")
        sos2.write(f"🛵 **CEEs/Society:** Avg: {soc_view['CEEs'].mean():.1f}")
        st.dataframe(soc_view.sort_values('Orders', ascending=False), width='stretch')

    # --- TAB 7: ORDER DETAIL ---
    with tab7:
        st.subheader("Order Audit Trail")
        od = df_act.copy()
        od['Bin_Time'] = od['order_binned_time'].dt.strftime('%H:%M')
        od['Asg_Time'] = od['assignment_to_Cee_time'].dt.strftime('%H:%M')
        od['Deliv_Time'] = od['order_delivered_time'].dt.strftime('%H:%M')
        st.dataframe(od[['order_id', 'sa_name', 'cee_name', 'Bin_Time', 'Asg_Time', 'Deliv_Time', 'Primary_RCA', 'Live_Status']], width='stretch')

else:
    st.info("Please upload the Delivery Report CSV in the sidebar.")
