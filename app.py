"""Streamlit entry point."""
import streamlit as st

from admin_panel import render_admin_panel
from auth import login_form, logout_button, required_password_change
from github_sync import GitHubStore, GitHubSyncError
from user_panel import render_user_panel

st.set_page_config(page_title="OFP / BBZ BTS Dashboard", page_icon="📊", layout="wide")

try:
    store = GitHubStore()
except GitHubSyncError as exc:
    st.error(str(exc))
    st.stop()

if not st.session_state.get("authenticated"):
    login_form(store)
    st.stop()

logout_button()
if required_password_change(store):
    st.stop()

role = st.session_state.get("role")
if role == "admin":
    admin_page = st.sidebar.radio("Admin navigation", ["Admin Panel", "View Reports"])
    if admin_page == "Admin Panel":
        render_admin_panel(store)
    else:
        render_user_panel(store)
elif role == "user":
    render_user_panel(store)
else:
    st.error("This account has an invalid role. Contact an administrator.")
