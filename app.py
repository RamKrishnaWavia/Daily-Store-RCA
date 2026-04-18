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

    # Order-Level RCA
    def get_order_rca(row):
        if not row['Late']: return "On Time"
        if row['CEE Unavail']: return "CEE Unavailable"
        elif row['CEE Late']: return "CEE Late Reporting"
        elif row['Pick Delay']: return "Picking Delay"
        elif row['DC Delay']: return "DC Arrival Issue"
        else: return "Last Mile / Traffic"

    df_act['Order_RCA'] = df_act.apply(get_order_rca, axis=1)

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

    # 4. TABBED VIEW NAVIGATION
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "City Charts (Visuals)", "Store RCA", "Route Analysis", "CEE Performance", "Order Detail", "Timeline View"
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
                late_only = df_act_filtered[df_act_filtered['Late'] == True]
                rca_data = late_only['Order_RCA'].value_counts()
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
            CEE_Late=('CEE Late', 'sum')
        ).reset_index()

        store_data = store_gross.merge(store_act, on='sa_name', how='left').fillna(0)
        store_data['Late %'] = (store_data['Late_Orders'] / store_data['Actionable_Orders'] * 100).round(1).fillna(0)

        # RCA Logic
        late_orders = df_act_filtered[df_act_filtered['Late'] == True]
        if not late_orders.empty:
            store_rca_mode = late_orders.groupby('sa_name')['Order_RCA'].agg(lambda x: x.mode().iloc[0]).reset_index()
            store_rca_mode.rename(columns={'Order_RCA': 'Primary RCA'}, inplace=True)
            store_data = store_data.merge(store_rca_mode, on='sa_name', how='left')
            store_data['Primary RCA'] = store_data['Primary RCA'].fillna("On Track")
        else:
            store_data['Primary RCA'] = "On Track"

        cols = ['sa_name', 'Total_Orders', 'Cancelled', 'Actionable_Orders', 'Total_Societies', 'Late %', 'Primary RCA', 'Late_Orders', 'DC_Delay', 'Pick_Delay', 'CEE_Late']
        st.dataframe(store_data[cols].sort_values(by='Late %', ascending=False), use_container_width=True)

    # --- TAB 3: ROUTE ANALYSIS ---
    with tab3:
        st.subheader(f"Route Analysis: {selected_city}")
        
        route_data = df_act_filtered.groupby(['route_id', 'sa_name']).agg(
            Total_Orders=('order_id', 'count'),
            Delayed_Orders=('Late', 'sum'),
            Society_Count=('society_id', 'nunique')
        ).reset_index()
        
        route_data['On_Time_Orders'] = route_data['Total_Orders'] - route_data['Delayed_Orders']
        route_data['Route Status'] = route_data['Delayed_Orders'].apply(lambda x: "Delayed" if x > 0 else "On Time")
        
        # Route Summary Metrics
        r1, r2, r3 = st.columns(3)
        r1.metric("Total Routes Dispatched", f"{len(route_data)}")
        r2.metric("Routes 100% On Time", f"{(route_data['Delayed_Orders'] == 0).sum()}")
        r3.metric("Delayed Routes (At least 1 late)", f"{(route_data['Delayed_Orders'] > 0).sum()}")

        st.dataframe(route_data[['route_id', 'sa_name', 'Route Status', 'Society_Count', 'Total_Orders', 'On_Time_Orders', 'Delayed_Orders']].sort_values(by='Delayed_Orders', ascending=False), use_container_width=True)

    # --- TAB 4: CEE PERFORMANCE ---
    with tab4:
        st.subheader(f"CEE (Rider) Performance: {selected_city}")
        
        cee_data = df_act_filtered.groupby(['cee_id', 'cee_name']).agg(
            Routes_Delivered=('route_id', 'nunique'),
            Societies_Visited=('society_id', 'nunique'),
            First_Assign_Time=('assignment_to_Cee_time', 'min'),
            Last_Assign_Time=('assignment_to_Cee_time', 'max'),
            First_Delivery_Time=('order_delivered_time', 'min'),
            Last_Delivery_Time=('order_delivered_time', 'max')
        ).reset_index()

        # Get exact Delivered count
        delivered_series = df_act_filtered[df_act_filtered['order_status'] == 'complete'].groupby('cee_id').size()
        cee_data['Orders_Delivered'] = cee_data['cee_id'].map(delivered_series).fillna(0).astype(int)

        # Format Timestamps to read nicely (HH:MM:SS)
        for c in ['First_Assign_Time', 'Last_Assign_Time', 'First_Delivery_Time', 'Last_Delivery_Time']:
            cee_data[c] = cee_data[c].dt.strftime('%H:%M:%S').fillna('-')

        # Reorder columns
        cee_cols = ['cee_name', 'cee_id', 'Routes_Delivered', 'Orders_Delivered', 'Societies_Visited', 'First_Assign_Time', 'Last_Assign_Time', 'First_Delivery_Time', 'Last_Delivery_Time']
        st.dataframe(cee_data[cee_cols].sort_values(by='Orders_Delivered', ascending=False), use_container_width=True)

    # --- TAB 5: ORDER DETAIL ---
    with tab5:
        st.subheader(f"Order Detail List: {selected_city}")
        order_cols = ['order_id', 'sa_name', 'society_id', 'order_status', 'Order_RCA', 'order_binned_time', 'assignment_to_Cee_time', 'order_delivered_time']
        st.dataframe(df_act_filtered.sort_values(by='Late', ascending=False)[order_cols], use_container_width=True)

    # --- TAB 6: TIMELINE VIEW ---
    with tab6:
        st.subheader("Delivery Timeline View")
        delivered_df = df_act_filtered[df_act_filtered['order_status'] == 'complete'].copy()
        if not delivered_df.empty:
            delivered_df['hour_minute'] = delivered_df['order_delivered_time'].dt.strftime('%H:%M')
            timeline_data = delivered_df.groupby('hour_minute').size()
            st.line_chart(timeline_data)
        else:
            st.warning("No completed delivery times to chart.")

else:
    st.info("Please upload the CSV file in the sidebar to generate your UI tabs.")
