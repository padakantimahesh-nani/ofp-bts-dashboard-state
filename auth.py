"""Authentication, session handling, and bcrypt-backed credential storage."""
from __future__ import annotations

from typing import Any

import bcrypt
import streamlit as st
import yaml

from github_sync import GitHubStore

CREDENTIALS_PATH = "credentials.yaml"
SEED_USERS = {
    "OFP_Admin": {"display_name": "OFP Administrator", "role": "admin", "password": "OFP_ADMIN"},
    "OFP_User": {"display_name": "OFP User", "role": "user", "password": "Welcome@123"},
}


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def load_credentials(store: GitHubStore) -> dict[str, Any]:
    content, _ = store.get_file(CREDENTIALS_PATH)
    if content is None:
        data = {"users": {name: {"display_name": details["display_name"], "role": details["role"],
                                  "password_hash": hash_password(details["password"]), "must_change_password": True}
                          for name, details in SEED_USERS.items()}}
        save_credentials(store, data, "Seed dashboard accounts")
        return data
    parsed = yaml.safe_load(content.decode("utf-8")) or {}
    parsed.setdefault("users", {})
    return parsed


def save_credentials(store: GitHubStore, credentials: dict[str, Any], message: str) -> None:
    store.put_file(CREDENTIALS_PATH, yaml.safe_dump(credentials, sort_keys=False), message)


def verify_user(credentials: dict[str, Any], username: str, password: str) -> dict[str, Any] | None:
    user = credentials.get("users", {}).get(username)
    if not user:
        return None
    try:
        valid = bcrypt.checkpw(password.encode(), user["password_hash"].encode())
    except (KeyError, ValueError):
        valid = False
    return user if valid else None


def login_form(store: GitHubStore) -> None:
    st.title("OFP / BBZ BTS Dashboard")
    st.caption("Sign in with your dashboard username and password.")
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
    if submitted:
        credentials = load_credentials(store)
        user = verify_user(credentials, username.strip(), password)
        if user:
            st.session_state.authenticated = True
            st.session_state.username = username.strip()
            st.session_state.display_name = user.get("display_name", username.strip())
            st.session_state.role = user.get("role", "user")
            st.session_state.must_change_password = bool(user.get("must_change_password", False))
            st.rerun()
        st.error("Invalid username or password.")


def logout_button() -> None:
    st.sidebar.caption(f"Signed in as {st.session_state.get('display_name', '')}")
    if st.sidebar.button("Log out", use_container_width=True):
        for key in ("authenticated", "username", "display_name", "role", "must_change_password", "pivot_config"):
            st.session_state.pop(key, None)
        st.rerun()


def required_password_change(store: GitHubStore) -> bool:
    """Block access until a seeded/temporary password has been replaced."""
    if not st.session_state.get("must_change_password", False):
        return False
    st.title("Change temporary password")
    st.warning("You must replace your temporary password before opening the dashboard.")
    with st.form("required_password_change"):
        password = st.text_input("New password", type="password")
        confirm = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Change password", type="primary")
    if submitted:
        if len(password) < 8:
            st.error("Use at least 8 characters.")
        elif password != confirm:
            st.error("Passwords do not match.")
        else:
            credentials = load_credentials(store)
            username = st.session_state.username
            credentials["users"][username]["password_hash"] = hash_password(password)
            credentials["users"][username]["must_change_password"] = False
            save_credentials(store, credentials, f"Replace temporary password for {username}")
            st.session_state.must_change_password = False
            st.success("Password changed.")
            st.rerun()
    return True
