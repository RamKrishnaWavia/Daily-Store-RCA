import streamlit as st
import pandas as pd
import datetime

# --- 1. INITIAL SETUP ---
st.set_page_config(page_title="Personal Command Center", layout="wide")
st.title("🚚 Daily Delivery & RCA Command Center")

uploaded_file = st.sidebar.file_uploader("Upload Delivery Report (CSV)", type="csv")

if uploaded_file:
    df = pd.read_csv(uploaded_file)
    time_cols = ['slot_from_time', 'order_binned_time', 'assignment_to_Cee_time', 'order_delivered_time']
    for col in time_cols:
        df[col] = pd.to_datetime(df[col], errors='coerce')
    
    # --- 2. REACTIVE FILTERS ---
    df['delivery_date'] = df['slot_from_time'].dt.date
    min_date = df['delivery_date'].dropna().min()
    max_date = df['delivery_date'].dropna().max()
    
    st.sidebar.subheader("Filters")
    start_dt = st.sidebar.date_input("Start Date", min_date)
    end_dt = st.sidebar.date_input("End Date", max_date)
    
    cities = sorted(df['city_name'].dropna().unique().tolist())
    sel_city = st.sidebar.selectbox("Select City", ["All Cities"] + cities)

    mask = (df['delivery_date'] >= start_dt) & (df['delivery_date'] <= end_dt)
    df_f = df[mask].copy()
    if sel_city != "All Cities":
        df_f = df_f[df_f['city_name'] == sel_city]

    if df_f.empty:
        st.warning("No records found for the selected criteria.")
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

    OTD_LIMIT, PICK_LIMIT, REPORT_CUTOFF = datetime.time(7, 0), datetime.time(4, 0), datetime.time(4, 30)

    df_act['four_am'] = pd.to_datetime(df_act['delivery_date'].astype(str) + ' 04:00:00')
    df_act['wait_start'] = df_act[['order_binned_time', 'four_am']].max(axis=1)
    df_act['Eff_Wait'] = (df_act['assignment_to_Cee_time'] - df_act['wait_start']).dt.total_seconds() / 60
    df_act['Travel_Mins'] = (df_act['order_delivered_time'] - df_act['assignment_to_Cee_time']).dt.total_seconds() / 60

    # CEE First-Assignment Logic
    cee_day = df_act.groupby(['delivery_date', 'cee_id'])['assignment_to_Cee_time'].min().reset_index()
    cee_day.rename(columns={'assignment_to_Cee_time': 'first_asg'}, inplace=True)
    cee_day['is_late_cee'] = cee_day['first_asg'].dt.time > REPORT_CUTOFF
    df_act = df_act.merge(cee_day, on=['delivery_date', 'cee_id'], how='left')

    # DC Arrival Logic
    dc_check = df_act.groupby(['delivery_date', 'sa_name'])['order_binned_time'].apply(lambda x: (x.dt.time > PICK_LIMIT).mean() > 0.5).reset_index()
    dc_check.rename(columns={'order_binned_time': 'is_dc_late'}, inplace=True)
    df_act = df_act.merge(dc_check, on=['delivery_date', 'sa_name'], how='left')

    df_act['Late'] = df_act['order_delivered_time'].apply(lambda x: x.time() > OTD_LIMIT if pd.notnull(x) else False)
    
    # RCA ENGINE
    def get_rca(row):
        if not row['Late']: return "On-time"
        no_rt = pd.isnull(row['route_id']) or row['route_id'] == 0
        if pd.notnull(row['order_binned_time']) and no_rt and row['Eff_Wait'] > 30: return "CEE Unavailable"
        if row.get('is_late_cee', False): return "CEE Late Reporting"
        if row['order_binned_time'].time() > PICK_LIMIT and not row['is_dc_late']: return "GRN / Picking Delay"
        if row['is_dc_late']: return "DC Arrival Issue"
        if row['Travel_Mins'] > 120: return "CEE Took More Time"
        return "Last Mile / Traffic"

    df_act['Primary_RCA'] = df_act.apply(get_rca, axis=1)

    # --- 4. TABS WITH SUMMARIES ---
    t_rca, t_city, t_slab, t_store, t_rt, t_cee, t_soc, t_od = st.tabs([
        "RCA Impact", "City Summary", "Delivery Slabs", "Store RCA", "Route Analysis", "CEE Performance", "Society Load", "Audit Log"
    ])

    with t_rca:
        st.subheader("📢 Executive Impact Summary")
        impact = df_act[df_act['Late']].groupby(['city_name', 'Primary_RCA']).agg(Orders=('order_id', 'count'), Routes=('route_id', 'nunique'), Stores=('sa_name', 'nunique')).reset_index()
        if not impact.empty:
            st.table(impact.sort_values(['city_name', 'Orders'], ascending=[True, False]))
        else: st.success("100% On-Time Performance!")

    with t_city:
        st.subheader("City-Level Status Snapshot")
        city_pivot = df_f.pivot_table(index='city_name', columns='Live_Status', values='order_id', aggfunc='count', fill_value=0).reset_index()
        city_stats = df_act.groupby('city_name').agg(Late_Rate=('Late', 'mean')).reset_index()
        c1, c2 = st.columns(2)
        c1.write(f"📈 **Avg Late %:** {(city_stats['Late_Rate'].mean()*100):.1f}%")
        c2.write(f"🏙️ **Cities Active:** {len(city_stats)}")
        st.dataframe(city_pivot, width='stretch')

    with t_slab:
        st.subheader("Delivery Slabs (Volume vs Timing)")
        df_act['Slab'] = df_act['order_delivered_time'].apply(lambda x: "Post 7 AM" if pd.notnull(x) and x.time() > datetime.time(7,0) else "Before 7 AM")
        slab_v = df_act.groupby('Slab').size().reset_index(name='Orders')
        st.metric("On-Time (Before 7 AM)", f"{slab_v[slab_v['Slab']=='Before 7 AM']['Orders'].sum():,}")
        st.bar_chart(slab_v.set_index('Slab'))

    with t_store:
        st.subheader("Store-Wise Performance Summary")
        store_v = df_act.groupby('sa_name').agg(Orders=('order_id', 'count'), Late_Rate=('Late', 'mean'), Avg_Wait=('Eff_Wait', 'mean')).reset_index()
        s1, s2 = st.columns(2)
        s1.write(f"🏢 **Worst Store Late %:** {(store_v['Late_Rate'].max()*100):.1f}%")
        s2.write(f"⏱️ **Avg Store Wait:** {int(store_v['Avg_Wait'].mean())} mins")
        st.dataframe(store_v.sort_values('Late_Rate', ascending=False), width='stretch')

    with t_rt:
        st.subheader("Route Productivity Stats")
        rt_view = df_act.groupby(['route_id', 'sa_name']).agg(Orders=('order_id', 'count'), Societies=('society_id', 'nunique'), Travel=('Travel_Mins', 'mean')).reset_index()
        r1, r2, r3 = st.columns(3)
        r1.metric("Avg Orders/Route", f"{int(rt_view['Orders'].mean())}")
        r2.metric("Avg Societies/Route", f"{rt_view['Societies'].mean():.1f}")
        r3.metric("Avg Travel Time", f"{int(rt_view['Travel'].mean())}m")
        st.dataframe(rt_view.sort_values('Orders', ascending=False), width='stretch')

    with t_cee:
        st.subheader("CEE Efficiency & Multi-Tripping")
        cee_view = df_act.groupby(['sa_name', 'cee_name']).agg(Trips=('route_id', 'nunique'), Orders=('order_id', 'count'), Start=('first_asg', 'min')).reset_index()
        ce1, ce2 = st.columns(2)
        ce1.write(f"🔄 **Multi-Tripping:** {((cee_view['Trips']>1).mean()*100):.1f}% riders did >1 trip")
        ce2.write(f"🚲 **Active CEEs:** {len(cee_view)}")
        cee_view['Start'] = cee_view['Start'].dt.strftime('%H:%M')
        st.dataframe(cee_view.sort_values('Trips', ascending=False), width='stretch')

    with t_soc:
        st.subheader("Society Load Analysis")
        soc_v = df_act.groupby(['society_id', 'sa_name']).agg(Total_Orders=('order_id', 'count'), Impacted=('Late', 'sum'), CEEs=('cee_id', 'nunique')).reset_index()
        soc_v['Impact %'] = (soc_v['Impacted'] / soc_v['Total_Orders'] * 100).round(1)
        so1, so2 = st.columns(2)
        so1.write(f"📦 **Avg Load/Society:** {int(soc_v['Total_Orders'].mean())} orders")
        so2.write(f"⚠️ **Avg Society Impact:** {soc_v['Impact %'].mean():.1f}%")
        st.dataframe(soc_v.sort_values('Total_Orders', ascending=False), width='stretch')

    with t_od:
        st.subheader("Full Order Audit Log")
        st.write(f"🔍 **Total Actionable Records:** {len(df_act)}")
        st.dataframe(df_act[['order_id', 'sa_name', 'cee_name', 'Primary_RCA', 'Live_Status']], width='stretch')

else: st.info("Please upload the CSV to begin.")
