"""Streamlit visual theme for Portfolio Analytics v4."""

#______________________________________________________________________________
# THEME CSS
#______________________________________________________________________________

APP_CSS = """
<style>
:root {
    --pa-navy: #082B4C;
    --pa-navy-2: #0E3A62;
    --pa-gold: #C99A35;
    --pa-bg: #F7F6F2;
    --pa-card: #FFFFFF;
    --pa-line: #E5E1D8;
    --pa-text: #14283F;
    --pa-muted: #6D7785;
}
html, body, [class*="css"] {
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.stApp { background: var(--pa-bg); color: var(--pa-text); }
.block-container { max-width: 1550px; padding-top: 1.15rem; padding-bottom: 3rem; }
.pa-hero {
    background: linear-gradient(105deg, #062B4D 0%, #0A355B 100%);
    border: 1px solid rgba(201,154,53,.38);
    border-radius: 16px;
    padding: 20px 26px;
    margin-bottom: 18px;
    box-shadow: 0 8px 22px rgba(8,43,76,.09);
}
.pa-hero h1 { color: #E0AF45 !important; margin: 0; font-size: 2rem; letter-spacing: -.025em; }
.pa-hero p { color: #F0C96B !important; margin: 7px 0 0; font-size: .96rem; font-weight: 520; }
h1, h2, h3, h4 { color: #1C2C40 !important; letter-spacing: -.018em; }
[data-testid="stMetric"] {
    background: #FFFFFF;
    border: 1px solid var(--pa-line);
    border-radius: 12px;
    padding: 13px 15px;
    box-shadow: 0 3px 10px rgba(8,43,76,.035);
    min-height: 100px;
}
[data-testid="stMetricLabel"] { color: #647184; font-weight: 600; }
[data-testid="stMetricValue"] { color: var(--pa-navy); font-weight: 740; }
[data-testid="stPlotlyChart"] {
    background: #FFFFFF !important;
    border: 1px solid var(--pa-line);
    border-radius: 13px;
    padding: .25rem;
    box-shadow: 0 3px 10px rgba(8,43,76,.03);
}
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    border: 1px solid var(--pa-line) !important;
    border-radius: 11px !important;
    overflow: hidden;
    background: #FFFFFF;
}
[data-testid="stFileUploaderDropzone"] {
    background: #FFFFFF !important;
    border: 1px dashed #D2CCC0 !important;
    border-radius: 12px !important;
}
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--pa-gold) !important; }
div.stButton > button, div.stDownloadButton > button {
    border-radius: 9px !important;
    font-weight: 650 !important;
}
div.stButton > button[kind="primary"] {
    background: var(--pa-gold) !important;
    border: 1px solid var(--pa-gold) !important;
    color: var(--pa-navy) !important;
}
.pa-insight {
    background: #FFFDF7;
    border-left: 3px solid #C99A35;
    border-radius: 8px;
    padding: .65rem .8rem;
    margin: .35rem 0 .9rem;
    color: #33465B;
    font-size: .90rem;
}
.pa-copilot {
    background: #FFFFFF;
    border: 1px solid var(--pa-line);
    border-top: 3px solid var(--pa-gold);
    border-radius: 13px;
    padding: .8rem .9rem;
}
.pa-small { color: var(--pa-muted); font-size: .83rem; }
.pa-section-note { color: var(--pa-muted); font-size: .88rem; margin-top: -.45rem; margin-bottom: .8rem; }
.pa-scenario-banner {
    background: #FFF8E7; border: 1px solid #E8D39B; border-left: 4px solid var(--pa-gold);
    border-radius: 10px; padding: .7rem .85rem; margin: .4rem 0 1rem; color: #33465B;
}
[data-testid="stPlotlyChart"] > div { min-height: 0 !important; }
</style>
"""

HERO_HTML = """
<div class="pa-hero">
  <h1>Portfolio Analytics</h1>
  <p>Deterministic accounting and risk analytics with an AI Copilot connected across the entire portfolio engine.</p>
</div>
"""
