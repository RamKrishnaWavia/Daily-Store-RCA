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

    # --- CONSTANTS & THRESHOLDS ---
    OTD_LIMIT = datetime.time(7, 0)
    PICK_LIMIT = datetime.time(4, 0)
    CEE_REPORT_TIME = datetime.time(4, 0)

    # --- CORE LOGIC CALCULATIONS ---
    # Actionable dataset (excluding cancellations)
    df_act = df_filtered[df_filtered['order_status'] != 'cancelled'].copy()
    
    # 1. Wait Start Logic: Clock starts at 4 AM or Bin Time (whichever is later)
    df_act['four_am'] = pd.to_datetime(df_act['delivery_date'].astype(str) + ' 04:00:00')
    df_act['wait_start'] = df_act[['order_binned_time', 'four_am']].max(axis=1)
    df_act['Effective_Wait_Mins'] = (df_act['assignment_to_Cee_time'] - df_act['wait_start']).dt.total_seconds() / 60
    
    # 2. Travel Duration
    df_act['Travel_Mins'] = (df_act['order_delivered_time'] - df_act['assignment_to_Cee_time']).dt.total_seconds() / 60

    # 3. Wave Picking / DC Arrival Logic (Store-wide Check)
    # If >50% of orders in a store are binned after 4AM, it's a DC Arrival Issue
    store_bin_stats = df_act.groupby('sa_name')['order_binned_time'].apply(
        lambda x: (x.dt.time > PICK_LIMIT).mean() > 0.50
    ).reset_index().rename(columns={'order_binned_time': 'is_dc_late_store'})
    df_act = df_act.merge(store_bin_stats, on='sa_name', how='left')

    # 4. Late & On-Time Flags
    df_act['Late'] = df_act['order_delivered_time'].apply(lambda x: x.time() > OTD_LIMIT if pd.notnull(x) else False)
    df_act['On_Time'] = (df_act['order_status'] == 'complete') & (~df_act['Late'])

    # 5. PRIMARY RCA LOGIC
    def calculate_rca(row):
        # 1. CEE Unavailable: Binned + No Route + Wait > 30 mins after 4 AM
        no_route = pd.isnull(row['route_id']) or row['route_id'] == 0
        if pd.notnull(row['order_binned_time']) and no_route and row['Effective_Wait_Mins'] > 30:
            return "CEE Unavailable"

        # 2. CEE Late Reporting: Assignment after 4:30 AM
        if pd.notnull(row['assignment_to_Cee_time']) and row['assignment_to_Cee_time'].time() > datetime.time(4, 30):
            return "CEE Late Reporting"

        # 3. Picking Delay: Delayed flag OR Binned after 4 AM (and store isn't DC Late)
        delayed_picking_flag = str(row.get('Delayed_picking', 'no')).lower() == 'yes'
        if row['order_binned_time'].time() > PICK_LIMIT:
            if not row['is_dc_late_store'] or delayed_picking_flag:
                return "GRN / Picking Delay"

        # 4. DC Arrival Issue: Bulk failure at store level
        if row['is_dc_late_store']:
            return "DC Arrival Issue"

        # 5. CEE Took More Time: Travel > 120 mins
        if row['Travel_Mins'] > 120:
            return "CEE Took More Time"

        return "Last Mile / Traffic"

    # Map RCA only to Late orders
    df_act['Primary_RCA'] = df_act.apply(lambda r: calculate_rca(r) if r['Late'] else "On-time", axis=1)

    # --- DASHBOARD HEADER (TILE CARDS) ---
    st.markdown(f"### 📊 Operational Overview: {selected_city}")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Actionable Orders", f"{len(df_act):,}")
    c2.metric("On-Time Rate", f"{(df_act['On_Time'].mean()*100):.1f}%")
    c3.metric("On-Time (Before 7AM)", f"{df_act['On_Time'].sum():,}")
    c4.metric("Total Routes", f"{df_filtered['route_id'].nunique():,}")
    c5.metric("Total CEEs", f"{df_filtered['cee_id'].nunique():,}")
    c6.metric("Total Societies", f"{df_filtered['society_id'].nunique():,}")

    st.divider()

    # --- TAB NAVIGATION ---
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "City Summary", "Delivery Slabs", "Store RCA", "Route Analysis", "CEE Performance", "Society Analysis", "Order Detail"
    ])

    # TAB 1: CITY SUMMARY
    with tab1:
        st.subheader("City-Wise Summary")
        city_summary = df_act.groupby('city_name').agg(
            Orders=('order_id', 'count'),
            On_Time=('On_Time', 'sum'),
            Late=('Late', 'sum'),
            Avg_Bin_Wait=('Effective_Wait_Mins', 'mean')
        ).reset_index()
        city_summary['Late %'] = (city_summary['Late'] / city_summary['Orders'] * 100).round(1)
        city_summary['Avg Bin Wait'] = city_summary['Avg_Bin_Wait'].apply(lambda x: f"{int(x)}m" if x > 0 else "-")
        st.dataframe(city_summary.sort_values('Late %', ascending=False), width='stretch')
        
        st.divider()
        st.markdown("**Primary RCA Breakdown (Late Orders)**")
        rca_counts = df_act[df_act['Late'] == True]['Primary_RCA'].value_counts()
        st.bar_chart(rca_counts)

    # TAB 2: DELIVERY SLABS
    with tab2:
        st.subheader("Delivery Time Slabs")
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
        slab_view = df_act[df_act['Slab'] != "Undelivered"].groupby('Slab').agg(
            Orders=('order_id', 'count'), Stores=('sa_name', 'nunique'), Societies=('society_id', 'nunique')
        ).reset_index()
        st.dataframe(slab_view, width='stretch')
        st.bar_chart(slab_view.set_index('Slab')['Orders'])

    # TAB 3: STORE RCA
    with tab3:
        st.subheader("Store-Wise Performance")
        store_view = df_act.groupby('sa_name').agg(
            Orders=('order_id', 'count'),
            On_Time=('On_Time', 'sum'),
            Late=('Late', 'sum'),
            Societies=('society_id', 'nunique'),
            Avg_Wait=('Effective_Wait_Mins', 'mean')
        ).reset_index()
        store_view['Late %'] = (store_view['Late'] / store_view['Orders'] * 100).round(1)
        store_view['Avg Bin Wait'] = store_view['Avg_Wait'].apply(lambda x: f"{int(x)}m" if x > 0 else "-")
        
        # Get Primary RCA per store
        late_only = df_act[df_act['Late'] == True]
        if not late_only.empty:
            store_rca = late_only.groupby('sa_name')['Primary_RCA'].agg(lambda x: x.mode().iloc[0]).reset_index()
            store_view = store_view.merge(store_rca, on='sa_name', how='left')
        
        store_view['Primary_RCA'] = store_view['Primary_RCA'].fillna("On Track")
        st.dataframe(store_view[['sa_name', 'Orders', 'On_Time', 'Late', 'Late %', 'Societies', 'Avg Bin Wait', 'Primary_RCA']].sort_values('Late %', ascending=False), width='stretch')

    # TAB 4: ROUTE ANALYSIS
    with tab4:
        st.subheader("Route-Level Efficiency")
        route_view = df_act.groupby(['route_id', 'sa_name']).agg(
            Orders=('order_id', 'count'),
            Late=('Late', 'sum'),
            Societies=('society_id', 'nunique'),
            Avg_Wait=('Effective_Wait_Mins', 'mean'),
            Travel=('Travel_Mins', 'mean')
        ).reset_index()
        route_view['Late Rate'] = (route_view['Late'] / route_view['Orders'] * 100).round(0).astype(int).astype(str) + '%'
        route_view['Avg Bin Wait'] = route_view['Avg_Wait'].apply(lambda x: f"{int(x)}m" if x > 0 else "-")
        route_view['Avg Travel'] = route_view['Travel'].apply(lambda x: f"{int(x)}m" if x > 0 else "-")
        
        st.dataframe(route_view[['route_id', 'sa_name', 'Orders', 'Late Rate', 'Societies', 'Avg Bin Wait', 'Avg Travel']].sort_values('Orders', ascending=False), width='stretch')

    # TAB 5: CEE PERFORMANCE
    with tab5:
        st.subheader("CEE Reporting & Performance")
        cee_view = df_act.groupby(['cee_name', 'cee_id', 'sa_name']).agg(
            First_Asg=('assignment_to_Cee_time', 'min'),
            Orders=('order_id', 'count'),
            Societies=('society_id', 'nunique'),
            Late=('Late', 'sum')
        ).reset_index()
        
        def get_cee_slab(t):
            if pd.isnull(t): return "-"
            tm = t.time()
            if tm >= datetime.time(6, 0): return "> 06:00 AM"
            elif tm >= datetime.time(5, 30): return "> 05:30 AM"
            elif tm >= datetime.time(5, 0): return "> 05:00 AM"
            elif tm >= datetime.time(4, 30): return "> 04:30 AM"
            return "On-Time Start"
        
        cee_view['Reporting Slab'] = cee_view['First_Asg'].apply(get_cee_slab)
        cee_view['Late Rate %'] = (cee_view['Late'] / cee_view['Orders'] * 100).round(1)
        
        st.dataframe(cee_view[['cee_name', 'sa_name', 'Reporting Slab', 'Orders', 'Societies', 'Late Rate %']].sort_values('Late Rate %', ascending=False), width='stretch')

    # TAB 6: SOCIETY ANALYSIS
    with tab6:
        st.subheader("Society Fragmentation Analysis")
        soc_view = df_act.groupby(['society_id', 'sa_name']).agg(
            Orders=('order_id', 'count'),
            Routes=('route_id', 'nunique'),
            CEEs=('cee_id', 'nunique'),
            Late=('Late', 'sum')
        ).reset_index()
        soc_view.rename(columns={'Routes': 'No of Routes'}, inplace=True)
        st.dataframe(soc_view.sort_values('Orders', ascending=False), width='stretch')

    # TAB 7: ORDER DETAIL
    with tab7:
        st.subheader("Detailed Order Audit")
        od = df_act.copy()
        od['Bin_Time'] = od['order_binned_time'].dt.strftime('%H:%M')
        od['Assign_Time'] = od['assignment_to_Cee_time'].dt.strftime('%H:%M')
        od['Deliv_Time'] = od['order_delivered_time'].dt.strftime('%H:%M')
        
        od['Bin→Assign'] = od['Effective_Wait_Mins'].apply(lambda x: f"{int(x)}m" if pd.notnull(x) else "-")
        od['Assign→Deliver'] = od['Travel_Mins'].apply(lambda x: f"{int(x)}m" if pd.notnull(x) else "-")
        
        od_cols = ['order_id', 'sa_name', 'cee_name', 'Bin_Time', 'Assign_Time', 'Deliv_Time', 'Bin→Assign', 'Assign→Deliver', 'Primary_RCA']
        st.dataframe(od[od_cols], width='stretch')

else:
    st.info("Please upload the Delivery Report CSV in the sidebar.")
