import streamlit as st
import pandas as pd

# Set Page Config
st.set_page_config(page_title="Delivery RCA Dashboard", layout="wide")

st.title("🚚 Daily Delivery RCA Dashboard")
st.markdown("Upload your daily Express Order Report to analyze performance and root causes.")

# 1. Sidebar - File Upload
st.sidebar.header("Data Input")
uploaded_file = st.sidebar.file_uploader("Upload CSV", type="csv")

if uploaded_file:
    df = pd.read_csv(uploaded_file)
    
    # Pre-processing Timestamps
    time_cols = ['slot_from_time', 'order_binned_time', 'assignment_to_Cee_time', 'order_delivered_time', 'order_in_process_time']
    for col in time_cols:
        df[col] = pd.to_datetime(df[col], errors='coerce')

    # Constants & Logic limits
    OTD_LIMIT = pd.to_datetime('07:00:00').time()
    PICK_LIMIT = pd.to_datetime('04:00:00').time()
    ASGN_LIMIT = pd.to_datetime('06:15:00').time()

    # Base calculations
    df_act = df[df['order_status'] != 'cancelled'].copy()
    df_act['Late'] = df_act['order_delivered_time'].apply(lambda x: x.time() > OTD_LIMIT if pd.notnull(x) else False)
    df_act['DC Delay'] = df_act['order_in_process_time'].apply(lambda x: x.time() > pd.to_datetime('03:15:00').time() if pd.notnull(x) else False)
    df_act['Pick Delay'] = df_act['order_binned_time'].apply(lambda x: x.time() > PICK_LIMIT if pd.notnull(x) else False)
    df_act['CEE Late'] = df_act['assignment_to_Cee_time'].apply(lambda x: x.time() > ASGN_LIMIT if pd.notnull(x) else False)
    df_act['CEE Unavail'] = df_act['cee_id'].isnull()

    # 2. Summary Cards (Top Layer)
    total_act = len(df_act)
    on_time = (df_act['order_status'] == 'complete') & (~df_act['Late'])
    otd_pct = (on_time.sum() / total_act) * 100 if total_act > 0 else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Actionable Orders", f"{total_act:,}")
    col2.metric("On-Time Rate", f"{otd_pct:.1f}%")
    col3.metric("Late Deliveries", f"{df_act['Late'].sum():,}")
    col4.metric("Cancelled Orders", f"{(df['order_status'] == 'cancelled').sum():,}")

    st.divider()

    # 3. Universal City Filter
    cities = sorted(df['city_name'].dropna().unique())
    selected_city = st.selectbox("Select City for Analysis", ["All Cities"] + list(cities))

    if selected_city != "All Cities":
        df_act_filtered = df_act[df_act['city_name'] == selected_city]
    else:
        df_act_filtered = df_act

    # ==========================================
    # 4. TABBED VIEW NAVIGATION
    # ==========================================
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Store RCA", 
        "Order detail", 
        "CEE performance", 
        "Route analysis", 
        "Timeline view"
    ])

    # --- TAB 1: Store RCA ---
    with tab1:
        st.subheader(f"Store RCA: {selected_city}")
        store_data = df_act_filtered.groupby(['sa_name', 'city_name']).agg(
            Orders=('order_id', 'count'),
            Late_Qty=('Late', 'sum'),
            DC_Delay=('DC Delay', 'sum'),
            Pick_Delay=('Pick Delay', 'sum'),
            CEE_Late=('CEE Late', 'sum'),
            CEE_Unavail=('CEE Unavail', 'sum'),
            Undelivered=('order_status', lambda x: (x != 'complete').sum())
        ).reset_index()

        store_data['Late %'] = (store_data['Late_Qty'] / store_data['Orders'] * 100).round(1)

        def rca_logic(row):
            reasons = {'DC Arrival Issue': row['DC_Delay'], 'Picking Delay': row['Pick_Delay'], 'CEE Late Report': row['CEE_Late'], 'CEE Unavail': row['CEE_Unavail']}
            return max(reasons, key=reasons.get) if row['Late_Qty'] > 0 else "On Track"

        store_data['Primary RCA'] = store_data.apply(rca_logic, axis=1)
        st.dataframe(store_data, use_container_width=True)

    # --- TAB 2: Order Detail ---
    with tab2:
        st.subheader(f"Order Detail List: {selected_city}")
        order_cols = ['order_id', 'sa_name', 'order_status', 'order_in_process_time', 'order_binned_time', 'assignment_to_Cee_time', 'order_delivered_time', 'Late']
        st.dataframe(df_act_filtered[order_cols], use_container_width=True)

    # --- TAB 3: CEE Performance ---
    with tab3:
        st.subheader(f"CEE (Rider) Performance: {selected_city}")
        cee_data = df_act_filtered.groupby(['cee_id', 'cee_name']).agg(
            Total_Orders=('order_id', 'count'),
            Late_Deliveries=('Late', 'sum'),
            On_Time_Deliveries=('Late', lambda x: (~x).sum())
        ).reset_index()
        cee_data['Late %'] = (cee_data['Late_Deliveries'] / cee_data['Total_Orders'] * 100).round(1)
        # Sort by worst performing riders
        st.dataframe(cee_data.sort_values(by='Late_Deliveries', ascending=False), use_container_width=True)

    # --- TAB 4: Route Analysis ---
    with tab4:
        st.subheader(f"Route Analysis: {selected_city}")
        route_data = df_act_filtered.groupby(['route_id', 'sa_name']).agg(
            Total_Orders=('order_id', 'count'),
            Late_Orders=('Late', 'sum')
        ).reset_index()
        route_data['Route Late %'] = (route_data['Late_Orders'] / route_data['Total_Orders'] * 100).round(1)
        st.dataframe(route_data.sort_values(by='Late_Orders', ascending=False), use_container_width=True)

    # --- TAB 5: Timeline View ---
    with tab5:
        st.subheader("Timeline View Analytics")
        st.info("Displays delivery completion flow over time.")
        # Simple line chart showing when orders were delivered
        delivered_df = df_act_filtered[df_act_filtered['order_status'] == 'complete'].copy()
        if not delivered_df.empty:
            delivered_df['hour_minute'] = delivered_df['order_delivered_time'].dt.strftime('%H:%M')
            timeline_data = delivered_df.groupby('hour_minute').size()
            st.line_chart(timeline_data)
        else:
            st.warning("No completed delivery times to chart.")

else:
    st.info("Please upload the CSV file in the sidebar to generate your UI tabs.")
