import streamlit as st
import pandas as pd
import datetime

# Set Page Config
st.set_page_config(page_title="Delivery RCA Dashboard", layout="wide")

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

    # --- DATE FILTER LOGIC ---
    df['delivery_date'] = df['slot_from_time'].dt.date
    min_date = df['delivery_date'].dropna().min()
    max_date = df['delivery_date'].dropna().max()
    
    st.sidebar.subheader("Select Date Range")
    start_date = st.sidebar.date_input("From Date", min_date)
    end_date = st.sidebar.date_input("To Date", max_date)
    
    # Apply Date Filter
    df = df[(df['delivery_date'] >= start_date) & (df['delivery_date'] <= end_date)]
    
    if df.empty:
        st.warning("No data available for the selected date range.")
        st.stop()

    # Constants & Logic limits
    OTD_LIMIT = datetime.time(7, 0)
    PICK_LIMIT = datetime.time(4, 0)
    ASGN_LIMIT = datetime.time(6, 15)
    DC_LIMIT = datetime.time(3, 15)

    # 3. Universal City Filter
    cities = sorted(df['city_name'].dropna().unique())
    selected_city = st.sidebar.selectbox("Select City for Analysis", ["All Cities"] + list(cities))

    if selected_city != "All Cities":
        df_filtered = df[df['city_name'] == selected_city].copy()
    else:
        df_filtered = df.copy()

    # --- CORE LOGIC CALCULATIONS ---
    # Actionable Orders
    df_act_filtered = df_filtered[df_filtered['order_status'] != 'cancelled'].copy()
    
    # On-Time vs Late
    df_act_filtered['Late'] = df_act_filtered['order_delivered_time'].apply(lambda x: x.time() > OTD_LIMIT if pd.notnull(x) else False)
    df_act_filtered['On_Time'] = (df_act_filtered['order_status'] == 'complete') & (~df_act_filtered['Late'])
    
    # Bottlenecks
    df_act_filtered['DC Delay'] = df_act_filtered['order_in_process_time'].apply(lambda x: x.time() > DC_LIMIT if pd.notnull(x) else False)
    df_act_filtered['Pick Delay'] = df_act_filtered['order_binned_time'].apply(lambda x: x.time() > PICK_LIMIT if pd.notnull(x) else False)
    df_act_filtered['CEE Late'] = df_act_filtered['assignment_to_Cee_time'].apply(lambda x: x.time() > ASGN_LIMIT if pd.notnull(x) else False)
    df_act_filtered['CEE Unavail'] = df_act_filtered['cee_id'].isnull()
    
    # SMART BIN WAIT LOGIC (Starts at 4:00 AM CEE Reporting)
    df_act_filtered['four_am'] = df_act_filtered['order_binned_time'].dt.normalize() + pd.Timedelta(hours=4)
    df_act_filtered['effective_bin'] = df_act_filtered[['order_binned_time', 'four_am']].max(axis=1)
    raw_wait = (df_act_filtered['assignment_to_Cee_time'] - df_act_filtered['effective_bin']).dt.total_seconds() / 60
    df_act_filtered['Wait_Time_Mins'] = raw_wait.apply(lambda x: max(0, x) if pd.notnull(x) else x)

    # Order-Level RCA
    def get_order_rca(row):
        if not row['Late']: return "On Time"
        if row['CEE Unavail']: return "CEE Unavailable"
        elif row['CEE Late']: return "CEE Late Reporting"
        elif row['Pick Delay']: return "Picking Delay"
        elif row['DC Delay']: return "DC Arrival Issue"
        else: return "Last mile / traffic"

    df_act_filtered['Order_RCA'] = df_act_filtered.apply(get_order_rca, axis=1)

    # RCA Label Mapping
    rca_mapping = {
        "Picking Delay": "GRN / picking delay",
        "CEE Late Reporting": "CEE late reporting",
        "DC Arrival Issue": "DC arrival issue",
        "CEE Unavailable": "CEE unavailable",
        "Last mile / traffic": "Last mile / traffic",
        "On Track": "On Track"
    }

    # Status Classifier
    def classify_status(row):
        status = str(row['order_status']).lower()
        if status == 'cancelled': return 'Cancelled'
        if status in ['complete', 'delivered']: return 'Delivered'
        if status in ['ofd', 'dispatched']: return 'OFD'
        if status in ['binned', 'packed']: return 'Bin'
        return 'Open'

    df_filtered['Live_Status'] = df_filtered.apply(classify_status, axis=1)

    # ==========================================
    # DASHBOARD HEADER (TILE CARDS)
    # ==========================================
    total_act = len(df_act_filtered)
    on_time_total = df_act_filtered['On_Time'].sum()
    otd_pct = (on_time_total / total_act) * 100 if total_act > 0 else 0

    st.markdown(f"### 📊 Operational Overview: {selected_city}")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Actionable Orders", f"{total_act:,}")
    c2.metric("On-Time Rate", f"{otd_pct:.1f}%")
    c3.metric("On-Time (Before 7AM)", f"{on_time_total:,}")
    c4.metric("Late Orders", f"{df_act_filtered['Late'].sum():,}")
    c5.metric("Total Routes", f"{df_filtered['route_id'].nunique():,}")
    c6.metric("Total Societies", f"{df_filtered['society_id'].nunique():,}")

    st.markdown("### 📦 Live Order Pipeline")
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("📝 Open", f"{(df_filtered['Live_Status'] == 'Open').sum():,}")
    s2.metric("🛒 Bin", f"{(df_filtered['Live_Status'] == 'Bin').sum():,}")
    s3.metric("🛵 OFD", f"{(df_filtered['Live_Status'] == 'OFD').sum():,}")
    s4.metric("✅ Delivered", f"{(df_filtered['Live_Status'] == 'Delivered').sum():,}")
    s5.metric("❌ Cancelled", f"{(df_filtered['Live_Status'] == 'Cancelled').sum():,}")

    st.divider()

    # ==========================================
    # TAB NAVIGATION
    # ==========================================
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "City Summary", "Delivery Slabs", "Store RCA", "Route Analysis", "CEE Performance", "Society Analysis", "Order Detail"
    ])

    # --- TAB 1: CITY SUMMARY ---
    with tab1:
        st.subheader(f"City Summary: {selected_city}")
        city_gross = df_filtered.groupby('city_name').agg(Total_Orders=('order_id', 'count'), Cancelled=('order_status', lambda x: (x == 'cancelled').sum())).reset_index()
        city_act = df_act_filtered.groupby('city_name').agg(
            Actionable_Orders=('order_id', 'count'), 
            On_Time_Orders=('On_Time', 'sum'),
            Late_Orders=('Late', 'sum'), 
            Avg_Bin_Wait=('Wait_Time_Mins', 'mean'), 
            DC_Delay=('DC Delay', 'sum'), 
            Pick_Delay=('Pick Delay', 'sum'), 
            CEE_Late=('CEE Late', 'sum')
        ).reset_index()
        city_data = city_gross.merge(city_act, on='city_name', how='left').fillna(0)
        city_data['Late %'] = (city_data['Late_Orders'] / city_data['Actionable_Orders'] * 100).round(1).fillna(0)
        city_data['Avg Bin Wait'] = city_data['Avg_Bin_Wait'].apply(lambda x: f"{int(x)}m" if x > 0 else "-")
        city_data.rename(columns={'city_name': 'City'}, inplace=True)
        st.dataframe(city_data[['City', 'Total_Orders', 'Cancelled', 'Actionable_Orders', 'On_Time_Orders', 'Late %', 'Late_Orders', 'Avg Bin Wait', 'DC_Delay', 'Pick_Delay', 'CEE_Late']], use_container_width=True)
        
        st.divider()
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            st.markdown("**Late Orders by Store**")
            st.bar_chart(df_act_filtered.groupby('sa_name')['Late'].sum())
        with col_c2:
            st.markdown("**Primary RCA Breakdown (Late Orders)**")
            st.bar_chart(df_act_filtered[df_act_filtered['Late'] == True]['Order_RCA'].map(rca_mapping).value_counts())

    # --- TAB 2: DELIVERY SLABS ---
    with tab2:
        st.subheader(f"Delivery Time Slabs: {selected_city}")
        def get_slab(t):
            if pd.isnull(t): return "Undelivered"
            t = t.time()
            if t <= datetime.time(7, 0): return "Before 7:00 AM"
            elif t <= datetime.time(7, 30): return "7:00 - 7:30 AM"
            elif t <= datetime.time(8, 0): return "7:30 - 8:00 AM"
            elif t <= datetime.time(8, 30): return "8:00 - 8:30 AM"
            elif t <= datetime.time(9, 0): return "8:30 - 9:00 AM"
            else: return "Post 9:00 AM"
        
        df_act_filtered['Slab'] = df_act_filtered['order_delivered_time'].apply(get_slab)
        slab_data = df_act_filtered[df_act_filtered['Slab'] != "Undelivered"].groupby('Slab').agg(
            Orders=('order_id', 'count'), Stores=('sa_name', 'nunique'), Societies=('society_id', 'nunique')
        ).reset_index()
        st.dataframe(slab_data, use_container_width=True)
        st.bar_chart(slab_data.set_index('Slab')['Orders'])

    # --- TAB 3: STORE RCA ---
    with tab3:
        st.subheader(f"Store RCA: {selected_city}")
        st_gross = df_filtered.groupby('sa_name').agg(Total_Orders=('order_id', 'count'), Cancelled=('order_status', lambda x: (x == 'cancelled').sum()), Total_Societies=('society_id', 'nunique')).reset_index()
        st_act = df_act_filtered.groupby('sa_name').agg(
            Actionable_Orders=('order_id', 'count'), On_Time_Orders=('On_Time', 'sum'), Late_Orders=('Late', 'sum'), 
            DC_Delay=('DC Delay', 'sum'), Pick_Delay=('Pick Delay', 'sum'), CEE_Late=('CEE Late', 'sum'), Avg_Bin_Wait=('Wait_Time_Mins', 'mean')
        ).reset_index()
        st_data = st_gross.merge(st_act, on='sa_name', how='left').fillna(0)
        st_data['Late %'] = (st_data['Late_Orders'] / st_data['Actionable_Orders'] * 100).round(1).fillna(0)
        st_data['Avg Bin Wait'] = st_data['Avg_Bin_Wait'].apply(lambda x: f"{int(x)}m" if x > 0 else "-")
        
        late_only = df_act_filtered[df_act_filtered['Late'] == True]
        if not late_only.empty:
            store_mode = late_only.groupby('sa_name')['Order_RCA'].agg(lambda x: x.mode().iloc[0]).reset_index()
            store_mode['Primary RCA'] = store_mode['Order_RCA'].map(rca_mapping)
            st_data = st_data.merge(store_mode[['sa_name', 'Primary RCA']], on='sa_name', how='left')
        st_data['Primary RCA'] = st_data['Primary RCA'].fillna("On Track")
        st.dataframe(st_data[['sa_name', 'Total_Orders', 'Cancelled', 'Actionable_Orders', 'On_Time_Orders', 'Total_Societies', 'Late %', 'Primary RCA', 'Avg Bin Wait', 'Late_Orders', 'DC_Delay', 'Pick_Delay', 'CEE_Late']].sort_values('Late %', ascending=False), use_container_width=True)

    # --- TAB 4: ROUTE ANALYSIS ---
    with tab4:
        st.subheader(f"Route Analysis: {selected_city}")
        rt_data = df_act_filtered.groupby(['route_id', 'sa_name']).agg(
            CEEs=('cee_id', 'nunique'), Orders=('order_id', 'count'), Societies=('society_id', 'nunique'), Late_Orders=('Late', 'sum'),
            First_Bin=('order_binned_time', 'min'), Last_Del=('order_delivered_time', 'max'), Avg_Wait=('Wait_Time_Mins', 'mean')
        ).reset_index()
        rt_data['Late Rate'] = ((rt_data['Late_Orders'] / rt_data['Orders']) * 100).round(0).astype(int).astype(str) + '%'
        rt_data['First binned'] = rt_data['First_Bin'].dt.strftime('%H:%M').fillna('-')
        rt_data['Last delivered'] = rt_data['Last_Del'].dt.strftime('%H:%M').fillna('-')
        rt_data['Avg Bin Wait'] = rt_data['Avg_Wait'].apply(lambda x: f"{int(x)}m" if x > 0 else "-")
        
        if not late_only.empty:
            rt_mode = late_only.groupby('route_id')['Order_RCA'].agg(lambda x: x.mode().iloc[0]).reset_index()
            rt_mode['Primary RCA'] = rt_mode['Order_RCA'].map(rca_mapping)
            rt_data = rt_data.merge(rt_mode[['route_id', 'Primary RCA']], on='route_id', how='left')
        rt_data.rename(columns={'route_id': 'Route ID', 'sa_name': 'Store'}, inplace=True)
        st.dataframe(rt_data[['Route ID', 'Store', 'CEEs', 'Orders', 'Societies', 'Late Rate', 'First binned', 'Avg Bin Wait', 'Last delivered', 'Primary RCA']].sort_values('Orders', ascending=False), use_container_width=True)

    # --- TAB 5: CEE PERFORMANCE ---
    with tab5:
        st.subheader(f"CEE Performance: {selected_city}")
        cee_data = df_act_filtered.groupby(['cee_name', 'cee_id', 'sa_name']).agg(
            Routes=('route_id', 'nunique'), Orders=('order_id', 'count'), Societies=('society_id', 'nunique'), Late_Orders=('Late', 'sum'),
            First_Asg=('assignment_to_Cee_time', 'min'), Last_Del=('order_delivered_time', 'max')
        ).reset_index()
        cee_data['On-time'] = cee_data['Orders'] - cee_data['Late_Orders']
        cee_data['Late_Rate_Val'] = (cee_data['Late_Orders'] / cee_data['Orders']) * 100
        cee_data['Late rate'] = cee_data['Late_Rate_Val'].round(0).astype(int).astype(str) + '%'
        cee_data['First assigned'] = cee_data['First_Asg'].dt.strftime('%H:%M').fillna('-')
        cee_data['Last delivered'] = cee_data['Last_Del'].dt.strftime('%H:%M').fillna('-')
        
        def cee_flags(row):
            f = []
            if pd.notnull(row['First_Asg']) and row['First_Asg'].time() > datetime.time(5, 0): f.append("Late start >05:00")
            if row['Late_Rate_Val'] >= 50: f.append("High late rate")
            return " ".join(f)
        cee_data['Flags'] = cee_data.apply(cee_flags, axis=1)
        cee_data.rename(columns={'cee_name': 'CEE name', 'sa_name': 'Store'}, inplace=True)
        
        # FIXED: Sort the dataframe BEFORE subsetting the display columns
        cee_display = cee_data.sort_values('Late_Rate_Val', ascending=False)
        st.dataframe(cee_display[['CEE name', 'Store', 'Routes', 'Orders', 'Societies', 'On-time', 'Late rate', 'First assigned', 'Last delivered', 'Flags']], use_container_width=True)

    # --- TAB 6: SOCIETY ANALYSIS ---
    with tab6:
        st.subheader(f"Society Analysis: {selected_city}")
        soc_data = df_act_filtered.groupby(['society_id', 'sa_name']).agg(
            Orders=('order_id', 'count'), Late_Orders=('Late', 'sum'), CEEs=('cee_id', 'nunique'), Routes=('route_id', 'nunique'),
            First_Del=('order_delivered_time', 'min'), Last_Del=('order_delivered_time', 'max')
        ).reset_index()
        soc_data['Late rate'] = ((soc_data['Late_Orders'] / soc_data['Orders']) * 100).round(0).astype(int).astype(str) + '%'
        soc_data['First delivered'] = soc_data['First_Del'].dt.strftime('%H:%M').fillna('-')
        soc_data['Last delivered'] = soc_data['Last_Del'].dt.strftime('%H:%M').fillna('-')
        
        if not late_only.empty:
            soc_mode = late_only.groupby('society_id')['Order_RCA'].agg(lambda x: x.mode().iloc[0]).reset_index()
            soc_mode['Primary RCA'] = soc_mode['Order_RCA'].map(rca_mapping)
            soc_data = soc_data.merge(soc_mode[['society_id', 'Primary RCA']], on='society_id', how='left')
        soc_data.rename(columns={'society_id': 'Society ID', 'sa_name': 'Store', 'Routes': 'No of Routes'}, inplace=True)
        st.dataframe(soc_data[['Society ID', 'Store', 'Orders', 'Late rate', 'CEEs', 'No of Routes', 'First delivered', 'Last delivered', 'Primary RCA']].sort_values('Orders', ascending=False), use_container_width=True)

    # --- TAB 7: ORDER DETAIL ---
    with tab7:
        st.subheader(f"Order Detail: {selected_city}")
        od = df_filtered.copy()
        od['Binned'] = od['order_binned_time'].dt.strftime('%H:%M').fillna('—')
        od['Assigned'] = od['assignment_to_Cee_time'].dt.strftime('%H:%M').fillna('—')
        od['Delivered'] = od['order_delivered_time'].dt.strftime('%H:%M').fillna('—')
        
        b_a = (od['assignment_to_Cee_time'] - od['order_binned_time']).dt.total_seconds() / 60
        od['Bin→Assign'] = b_a.apply(lambda x: f"{int(x)}m" if pd.notnull(x) else '—')
        a_d = (od['order_delivered_time'] - od['assignment_to_Cee_time']).dt.total_seconds() / 60
        od['Assign→Deliver'] = a_d.apply(lambda x: f"{int(x)}m" if pd.notnull(x) else '—')
        
        def od_rca(row):
            if str(row['order_status']).lower() == 'cancelled': return "Cancelled"
            l = pd.notnull(row['order_delivered_time']) and row['order_delivered_time'].time() > datetime.time(7, 0)
            if not l and row['order_status'] == 'complete': return "On-time"
            if row['order_status'] != 'complete': return "Undelivered"
            # Late Logic
            if pd.isnull(row['cee_id']): return "CEE unavailable"
            if pd.notnull(row['assignment_to_Cee_time']) and row['assignment_to_Cee_time'].time() > datetime.time(6, 15): return "CEE late reporting"
            if pd.notnull(row['order_binned_time']) and row['order_binned_time'].time() > datetime.time(4, 0): return "GRN / picking delay"
            if pd.notnull(row['order_in_process_time']) and row['order_in_process_time'].time() > datetime.time(3, 15): return "DC arrival issue"
            return "Last mile / traffic"
        
        od['RCA'] = od.apply(od_rca, axis=1)
        od.rename(columns={'order_id': 'Order ID', 'sa_name': 'Store', 'Live_Status': 'Status', 'cee_name': 'CEE Name', 'route_id': 'Route'}, inplace=True)
        st.dataframe(od[['Order ID', 'Store', 'CEE Name', 'Route', 'Status', 'Binned', 'Assigned', 'Delivered', 'Bin→Assign', 'Assign→Deliver', 'RCA']], use_container_width=True)

else:
    st.info("Upload the CSV to begin.")
