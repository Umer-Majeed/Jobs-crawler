import streamlit as st
import pandas as pd
import sqlite3
import os

st.set_page_config(
    page_title="Project Titan - Executive Dashboard",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
    .main { background-color: #f8fafc; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    </style>
""", unsafe_allow_html=True)

st.title("🚀 Project Titan | AI Job Discovery & Application Tracker")
st.markdown("Real-time executive dashboard tracking automated remote Python & Backend job prospecting.")

@st.cache_data(ttl=60)
def load_data():
    if os.path.exists("titan_application_tracker.csv"):
        return pd.read_csv("titan_application_tracker.csv")
    return pd.DataFrame()

df = load_data()

st.sidebar.header("🔍 Filter Options")
if not df.empty:
    min_score = st.sidebar.slider("Minimum Match Score", 0, 100, 50)
    remote_only = st.sidebar.checkbox("100% Fully Remote Only", value=True)
    filtered_df = df[df["Match Score"] >= min_score] if "Match Score" in df.columns else df
    if remote_only and "Fully Remote" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["Fully Remote"] == True]
else:
    filtered_df = df

col1, col2, col3, col4 = st.columns(4)
total_jobs = len(df)
eligible_jobs = len(df[df["Apply Recommended"] == True]) if not df.empty and "Apply Recommended" in df.columns else 0
avg_score = int(df["Match Score"].mean()) if not df.empty and total_jobs > 0 and "Match Score" in df.columns else 0
processed_db_count = total_jobs
try:
    conn = sqlite3.connect("titan_memory.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM processed_jobs")
    processed_db_count = cursor.fetchone()[0]
    conn.close()
except:
    pass

col1.metric("Total Jobs Scanned", total_jobs)
col2.metric("Eligible Matches", eligible_jobs)
col3.metric("Average Match Score", f"{avg_score}%")
col4.metric("Memory DB Tracked", processed_db_count)

st.divider()
st.subheader("📋 Application Tracking Pipeline")

if not filtered_df.empty:
    # Safe column selection based on what columns actually exist in the dataframe
    available_cols = [col for col in ["Company", "Job Title", "Match Score", "Estimated Salary", "Tech Stack", "Fully Remote", "Apply Recommended"] if col in filtered_df.columns]
    
    st.dataframe(
        filtered_df[available_cols],
        use_container_width=True,
        hide_index=True
    )
    
    st.subheader("📄 Cover Letter & Direct Job Links")
    selected_job_idx = st.selectbox("Select job to view generated pitch and link:", options=filtered_df.index, format_func=lambda x: f"{filtered_df.loc[x, 'Company'] if 'Company' in filtered_df.columns else 'Company'} - {filtered_df.loc[x, 'Job Title'] if 'Job Title' in filtered_df.columns else 'Role'} ({filtered_df.loc[x, 'Match Score'] if 'Match Score' in filtered_df.columns else 0}%)")
    
    if selected_job_idx is not None:
        row = filtered_df.loc[selected_job_idx]
        col_a, col_b = st.columns([2, 1])
        with col_a:
            st.markdown(f"**Company:** {row.get('Company', 'N/A')}")
            st.markdown(f"**Role:** {row.get('Job Title', 'N/A')}")
            st.markdown(f"**Estimated Salary:** {row.get('Estimated Salary', 'N/A')}")
            st.markdown(f"**Tech Stack:** {row.get('Tech Stack', 'N/A')}")
            cover_file = row.get('Cover Letter File', 'N/A')
            if pd.notna(cover_file) and cover_file != 'N/A' and os.path.exists(cover_file):
                with open(cover_file, 'r', encoding='utf-8') as cf:
                    st.text_area("Generated Humanized Pitch / Cover Letter", cf.read(), height=200)
            else:
                st.info("No cover letter generated for this entry.")
        with col_b:
            st.markdown("### Actions")
            job_url = row.get('Job URL', '#')
            if pd.notna(job_url) and job_url != 'N/A':
                st.markdown(f'<a href="{job_url}" target="_blank"><button style="background-color:#2563eb; color:white; padding:10px 20px; border:none; border-radius:5px; font-weight:bold; cursor:pointer;">🚀 Open Direct Apply Link</button></a>', unsafe_allow_html=True)
            else:
                st.warning("Direct URL not available.")
else:
    st.info("No jobs found matching criteria or database is empty.")