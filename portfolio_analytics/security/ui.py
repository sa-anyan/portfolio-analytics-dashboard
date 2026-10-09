"""Neutralise spreadsheet injection only in rendered/exported table copies."""
import streamlit as st
from .uploads import spreadsheet_safe


def safe_dataframe(frame, **kwargs):
    return st.dataframe(spreadsheet_safe(frame), **kwargs)
