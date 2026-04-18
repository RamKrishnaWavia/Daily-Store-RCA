import streamlit as st
import pandas as pd

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
    time_cols = ['slot_from_time', 'order_binned_time', 'assignment_to_Cee_time', 'order_delivered_time', 'order_in_process_time']
    for col in time_cols:
        df[col] = pd.to_datetime(df[col], errors='coerce')

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
    OTD_LIMIT = pd.to_datetime('07:00:00').time()
    PICK_LIMIT = pd.to_datetime('04:00:00').time()
    ASGN_LIMIT = pd.to_datetime('06:15:00').time()
    DC_LIMIT = pd.to_datetime('03:15:00').time()

    # Base calculations (Actionable Orders)
    df_act = df[df['order_status'] != 'cancelled'].copy()
    df_act['Late'] = df_act['order_delivered_time'].apply(lambda x: x.time() > OTD_LIMIT if pd.notnull(x) else False)
    df_act['DC Delay'] = df_act['order_in_process_time'].apply(lambda x: x.time() > DC_LIMIT if pd.notnull(x) else False)
    df_act['Pick Delay'] = df_act['order_binned_time'].apply(lambda x: x.time() > PICK_LIMIT if pd.notnull(x) else False)
    df_act['CEE Late'] = df_act['assignment_to_Cee_time'].apply(lambda x: x.time() > ASGN_LIMIT if pd.notnull(x) else False)
    df_act['CEE Unavail'] = df_act['cee_id'].isnull()
    
    # NEW LOGIC: Bin Waiting Time (Minutes)
    df_act['Wait_Time_Mins'] = (df_act['assignment_to_Cee_time'] - df_act['order_binned_time']).dt.total_seconds() / 60

    # Order-Level RCA
    def get_order_rca(row):
        if not row['Late']: return "On Time"
        if row['CEE Unavail']: return "CEE Unavailable"
        elif row['CEE Late']: return "CEE Late Reporting"
        elif row['Pick Delay']: return "Picking Delay"
        elif row['DC Delay']: return "DC Arrival Issue"
        else: return "Last Mile / Traffic"

    df_act['Order_RCA'] = df_act.apply(get_order_rca, axis=1)

    # RCA Label Mapping
    rca_mapping = {
        "Picking Delay": "GRN / picking delay",
        "CEE Late Reporting": "CEE late reporting",
        "DC Arrival Issue": "DC arrival issue",
        "CEE Unavailable": "CEE unavailable",
        "Last Mile / Traffic": "Last mile / traffic",
        "On Track": "On Track"
    }

    # 2. Summary Cards (Top Layer)
    total_act = len(df_act)
    on_time = (df_act['order_status'] == 'complete') & (~df_act['Late'])
    otd_pct = (on_time.sum() / total_act) * 100 if total_act > 0 else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Actionable Orders", f"{total_act:,}")
    col2.metric("On-Time Rate", f"{otd_pct:.1f}%")
    col3.metric("Late Orders", f"{df_act['Late'].sum():,}")
    col4.metric("Cancelled Orders", f"{(df['order_status'] == 'cancelled').sum():,}")

    st.divider()

    # 3. Universal City Filter
    cities = sorted(df['city_name'].dropna().unique())
    selected_city = st.selectbox("Select City for Analysis", ["All Cities"] + list(cities))

    if selected_city != "All Cities":
        df_filtered = df[df['city_name'] == selected_city]
        df_act_filtered = df_act[df_act['city_name'] == selected_city]
    else:
        df_filtered = df
        df_act_filtered = df_act

    # 4. TABBED VIEW NAVIGATION (Timeline removed)
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "City Charts (Visuals)", "Store RCA", "Route Analysis", "CEE Performance", "Society Analysis", "Order Detail"
    ])

    # --- TAB 1: CITY CHARTS ---
    with tab1:
        st.subheader(f"Performance Graphs: {selected_city}")
        if not df_act_filtered.empty:
            c1, c2 = st.columns(2)
            
            with c1:
                st.markdown("**Late Orders by Store**")
                chart_data = df_act_filtered.groupby('sa_name')['Late'].sum()
                st.bar_chart(chart_data)
                
            with c2:
                st.markdown("**Primary RCA Breakdown (Late Orders)**")
                late_only = df_act_filtered[df_act_filtered['Late'] == True].copy()
                late_only['Mapped RCA'] = late_only['Order_RCA'].map(rca_mapping).fillna(late_only['Order_RCA'])
                rca_data = late_only['Mapped RCA'].value_counts()
                st.bar_chart(rca_data)
        else:
            st.warning("No data to graph for this selection.")

    # --- TAB 2: STORE RCA ---
    with tab2:
        st.subheader(f"Store RCA: {selected_city}")
        
        # Gross metrics (including cancelled)
        store_gross = df_filtered.groupby('sa_name').agg(
            Total_Orders=('order_id', 'count'),
            Cancelled=('order_status', lambda x: (x == 'cancelled').sum()),
            Total_Societies=('society_id', 'nunique')
        ).reset_index()

        # Actionable metrics
        store_act = df_act_filtered.groupby('sa_name').agg(
            Actionable_Orders=('order_id', 'count'),
            Late_Orders=('Late', 'sum'),
            DC_Delay=('DC Delay', 'sum'),
            Pick_Delay=('Pick Delay', 'sum'),
            CEE_Late=('CEE Late', 'sum'),
            Avg_Bin_Wait=('Wait_Time_Mins', 'mean')  # <--- Added Wait Time
        ).reset_index()

        store_data = store_gross.merge(store_act, on='sa_name', how='left').fillna(0)
        store_data['Late %'] = (store_data['Late_Orders'] / store_data['Actionable_Orders'] * 100).round(1).fillna(0)
        store_data['Avg Bin Wait'] = store_data['Avg_Bin_Wait'].apply(lambda x: f"{int(x)}m" if pd.notnull(x) and x != 0 else "-")

        # RCA Logic
        late_orders = df_act_filtered[df_act_filtered['Late'] == True]
        if not late_orders.empty:
            store_rca_mode = late_orders.groupby('sa_name')['Order_RCA'].agg(lambda x: x.mode().iloc[0]).reset_index()
            store_rca_mode.rename(columns={'Order_RCA': 'Primary RCA'}, inplace=True)
            store_rca_mode['Primary RCA'] = store_rca_mode['Primary RCA'].map(rca_mapping).fillna(store_rca_mode['Primary RCA'])
            store_data = store_data.merge(store_rca_mode, on='sa_name', how='left')
            store_data['Primary RCA'] = store_data['Primary RCA'].fillna("On Track")
        else:
            store_data['Primary RCA'] = "On Track"

        cols = ['sa_name', 'Total_Orders', 'Cancelled', 'Actionable_Orders', 'Total_Societies', 'Late %', 'Primary RCA', 'Avg Bin Wait', 'Late_Orders', 'DC_Delay', 'Pick_Delay', 'CEE_Late']
        st.dataframe(store_data[cols].sort_values(by='Late %', ascending=False), use_container_width=True)

    # --- TAB 3: ROUTE ANALYSIS ---
    with tab3:
        st.subheader(f"Route Analysis: {selected_city}")
        
        route_data = df_act_filtered.groupby(['route_id', 'sa_name']).agg(
            CEEs=('cee_id', 'nunique'),
            Orders=('order_id', 'count'),
            Societies=('society_id', 'nunique'),
            Late_Orders=('Late', 'sum'),
            First_Binned_Time=('order_binned_time', 'min'),
            Last_Delivery_Time=('order_delivered_time', 'max'),
            Avg_Bin_Wait=('Wait_Time_Mins', 'mean') # <--- Added Wait Time
        ).reset_index()

        route_data['Late_Rate_Num'] = (route_data['Late_Orders'] / route_data['Orders']) * 100
        route_data['Late rate'] = route_data['Late_Rate_Num'].round(0).astype(int).astype(str) + '%'

        route_data['First binned'] = route_data['First_Binned_Time'].dt.strftime('%H:%M').fillna('-')
        route_data['Last delivered'] = route_data['Last_Delivery_Time'].dt.strftime('%H:%M').fillna('-')
        route_data['Avg Bin Wait'] = route_data['Avg_Bin_Wait'].apply(lambda x: f"{int(x)}m" if pd.notnull(x) else "-")

        if not late_orders.empty:
            route_rca_mode = late_orders.groupby('route_id')['Order_RCA'].agg(
                lambda x: x.mode().iloc[0] if not x.empty else "On Track"
            ).reset_index()
            route_rca_mode.rename(columns={'Order_RCA': 'Primary RCA'}, inplace=True)
            route_rca_mode['Primary RCA'] = route_rca_mode['Primary RCA'].map(rca_mapping).fillna(route_rca_mode['Primary RCA'])
            
            route_data = route_data.merge(route_rca_mode, on='route_id', how='left')
            route_data['Primary RCA'] = route_data['Primary RCA'].fillna("On Track")
        else:
            route_data['Primary RCA'] = "On Track"

        route_data.rename(columns={'route_id': 'Route ID', 'sa_name': 'Store'}, inplace=True)
        route_cols = ['Route ID', 'Store', 'CEEs', 'Orders', 'Societies', 'Late rate', 'First binned', 'Avg Bin Wait', 'Last delivered', 'Primary RCA']
        
        st.dataframe(route_data.sort_values(by=['Late_Rate_Num', 'Orders'], ascending=[False, False])[route_cols], use_container_width=True)

    # --- TAB 4: CEE PERFORMANCE ---
    with tab4:
        st.subheader(f"CEE (Rider) Performance: {selected_city}")
        
        cee_data = df_act_filtered.groupby(['cee_name', 'cee_id', 'sa_name']).agg(
            Routes=('route_id', 'nunique'),
            Orders=('order_id', 'count'),
            Late_Deliveries=('Late', 'sum'),
            First_Assign_Time=('assignment_to_Cee_time', 'min'),
            Last_Delivery_Time=('order_delivered_time', 'max')
        ).reset_index()

        cee_data['On-time'] = cee_data['Orders'] - cee_data['Late_Deliveries']
        cee_data['Late_Rate_Num'] = (cee_data['Late_Deliveries'] / cee_data['Orders']) * 100
        cee_data['Late rate'] = cee_data['Late_Rate_Num'].round(0).astype(int).astype(str) + '%'

        cee_data['First assigned'] = cee_data['First_Assign_Time'].dt.strftime('%H:%M').fillna('-')
        cee_data['Last delivered'] = cee_data['Last_Delivery_Time'].dt.strftime('%H:%M').fillna('-')

        def generate_flags(row):
            flags = []
            if pd.notnull(row['First_Assign_Time']) and row['First_Assign_Time'].time() > pd.to_datetime('05:00:00').time():
                flags.append("Late start >05:00")
            if row['Late_Rate_Num'] >= 50:
                flags.append("High late rate")
            return " ".join(flags)

        cee_data['Flags'] = cee_data.apply(generate_flags, axis=1)

        cee_data.rename(columns={'cee_name': 'CEE name', 'sa_name': 'Store'}, inplace=True)
        cee_cols = ['CEE name', 'Store', 'Routes', 'Orders', 'On-time', 'Late rate', 'First assigned', 'Last delivered', 'Flags']
        
        st.dataframe(cee_data.sort_values(by=['Late_Rate_Num', 'Orders'], ascending=[False, False])[cee_cols], use_container_width=True)

    # --- TAB 5: SOCIETY ANALYSIS ---
    with tab5:
        st.subheader(f"Society Level Analysis: {selected_city}")
        
        society_data = df_act_filtered.groupby(['society_id', 'sa_name']).agg(
            Orders=('order_id', 'count'),
            Late_Orders=('Late', 'sum'),
            CEEs_Visited=('cee_id', 'nunique'),
            Routes_Mapped=('route_id', 'nunique'),
            First_Delivery_Time=('order_delivered_time', 'min'),
            Last_Delivery_Time=('order_delivered_time', 'max')
        ).reset_index()

        society_data['Late_Rate_Num'] = (society_data['Late_Orders'] / society_data['Orders']) * 100
        society_data['Late rate'] = society_data['Late_Rate_Num'].round(0).astype(int).astype(str) + '%'

        society_data['First delivered'] = society_data['First_Delivery_Time'].dt.strftime('%H:%M').fillna('-')
        society_data['Last delivered'] = society_data['Last_Delivery_Time'].dt.strftime('%H:%M').fillna('-')

        if not late_orders.empty:
            soc_rca_mode = late_orders.groupby('society_id')['Order_RCA'].agg(
                lambda x: x.mode().iloc[0] if not x.empty else "On Track"
            ).reset_index()
            soc_rca_mode.rename(columns={'Order_RCA': 'Primary RCA'}, inplace=True)
            soc_rca_mode['Primary RCA'] = soc_rca_mode['Primary RCA'].map(rca_mapping).fillna(soc_rca_mode['Primary RCA'])
            
            society_data = society_data.merge(soc_rca_mode, on='society_id', how='left')
            society_data['Primary RCA'] = society_data['Primary RCA'].fillna("On Track")
        else:
            society_data['Primary RCA'] = "On Track"

        society_data['society_id'] = society_data['society_id'].astype(str).str.replace('.0', '', regex=False)
        society_data.rename(columns={'society_id': 'Society ID', 'sa_name': 'Store', 'CEEs_Visited': 'CEEs'}, inplace=True)
        
        soc_cols = ['Society ID', 'Store', 'Orders', 'Late rate', 'CEEs', 'Routes_Mapped', 'First delivered', 'Last delivered', 'Primary RCA']
        st.dataframe(society_data.sort_values(by=['Late_Rate_Num', 'Orders'], ascending=[False, False])[soc_cols], use_container_width=True)

    # --- TAB 6: ORDER DETAIL ---
    with tab6:
        st.subheader(f"Order Wise Analysis: {selected_city}")
        
        order_data = df_filtered.copy()
        order_data['Route'] = order_data['route_id'].fillna(0).astype(int).astype(str).replace('0', '-')
        order_data['CEE Name'] = order_data['cee_name'].fillna('-')
        
        order_data['Binned'] = order_data['order_binned_time'].dt.strftime('%H:%M').fillna('—')
        order_data['Assigned'] = order_data['assignment_to_Cee_time'].dt.strftime('%H:%M').fillna('—')
        order_data['Delivered'] = order_data['order_delivered_time'].dt.strftime('%H:%M').fillna('—')
        
        bin_assign = (order_data['assignment_to_Cee_time'] - order_data['order_binned_time']).dt.total_seconds() / 60
        order_data['Bin→Assign'] = bin_assign.apply(lambda x: f"{int(x)}m" if pd.notnull(x) else '—')
        
        assign_del = (order_data['order_delivered_time'] - order_data['assignment_to_Cee_time']).dt.total_seconds() / 60
        order_data['Assign→Deliver'] = assign_del.apply(lambda x: f"{int(x)}m" if pd.notnull(x) else '—')

        def get_detailed_order_rca(row):
            if row['order_status'] == 'cancelled': return "Cancelled"
            late = pd.notnull(row['order_delivered_time']) and row['order_delivered_time'].time() > pd.to_datetime('07:00:00').time()
            if not late and row['order_status'] == 'complete': return "On-time"
            if row['order_status'] != 'complete': return "Undelivered" 
            
            cee_unavail = pd.isnull(row['cee_id'])
            cee_late = pd.notnull(row['assignment_to_Cee_time']) and row['assignment_to_Cee_time'].time() > pd.to_datetime('06:15:00').time()
            pick_delay = pd.notnull(row['order_binned_time']) and row['order_binned_time'].time() > pd.to_datetime('04:00:00').time()
            dc_delay = pd.notnull(row['order_in_process_time']) and row['order_in_process_time'].time() > pd.to_datetime('03:15:00').time()

            if cee_unavail: return "CEE unavailable"
            elif cee_late: return "CEE late reporting"
            elif pick_delay: return "GRN / picking delay"
            elif dc_delay: return "DC arrival issue"
            else: return "Last mile / traffic"

        order_data['RCA'] = order_data.apply(get_detailed_order_rca, axis=1)
        
        order_data.rename(columns={'order_id': 'Order ID', 'sa_name': 'Store', 'order_status': 'Status'}, inplace=True)
        order_cols = ['Order ID', 'Store', 'CEE Name', 'Route', 'Status', 'Binned', 'Assigned', 'Delivered', 'Bin→Assign', 'Assign→Deliver', 'RCA']
        
        st.dataframe(order_data[order_cols], use_container_width=True)

else:
    st.info("Please upload the CSV file in the sidebar to generate your UI tabs.")
