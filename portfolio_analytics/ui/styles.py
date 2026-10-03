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
    background: linear-gradient(180deg, #FFFFFF 0%, #FCFBF8 100%);
    border: 1px solid #E1DDD3;
    border-top: 2px solid rgba(201,154,53,.72);
    border-radius: 14px;
    padding: 14px 16px 12px;
    box-shadow: 0 7px 22px rgba(8,43,76,.055);
    min-height: 108px;
    overflow: visible;
}
[data-testid="stMetricLabel"] {
    color: #697587;
    font-weight: 650;
    font-size: .82rem;
    letter-spacing: .015em;
}
[data-testid="stMetricValue"] {
    color: var(--pa-navy);
    font-weight: 760;
    font-size: clamp(1.45rem, 2.15vw, 2.25rem);
    line-height: 1.08;
    letter-spacing: -.035em;
}
[data-testid="stMetricValue"] > div {
    overflow: visible !important;
    text-overflow: clip !important;
    white-space: nowrap !important;
}
[data-testid="stMetric"] + [data-testid="stCaptionContainer"],
[data-testid="column"] [data-testid="stCaptionContainer"] {
    color: #7A8492;
    font-size: .73rem;
}
[data-testid="stPlotlyChart"] {
    background: linear-gradient(180deg, #FFFFFF 0%, #FDFCF9 100%) !important;
    border: 1px solid #E1DDD3;
    border-radius: 15px;
    padding: .35rem;
    box-shadow: 0 7px 22px rgba(8,43,76,.045);
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

.pa-finance-kpi {
    min-height: 92px;
    padding: 4px 4px 10px 0;
    border-bottom: 1px solid #E5E1D8;
}
.pa-finance-kpi-label {
    color: #6D7785;
    font-size: .78rem;
    font-weight: 650;
    letter-spacing: .01em;
    margin-bottom: 4px;
}
.pa-finance-kpi-value {
    color: #14283F;
    font-size: clamp(1.32rem, 2vw, 1.85rem);
    line-height: 1.08;
    font-weight: 760;
    letter-spacing: -.035em;
    white-space: nowrap;
}
.pa-finance-kpi-exact {
    color: #7B8490;
    font-size: .72rem;
    margin-top: 6px;
    white-space: nowrap;
}
@media (max-width: 900px) {
    .pa-finance-kpi { min-height: 82px; }
    .pa-finance-kpi-value { font-size: 1.28rem; }
}

</style>
"""

HERO_HTML = """
<div class="pa-hero">
  <h1>Portfolio Analytics</h1>
  <p>Deterministic accounting and risk analytics with an AI Copilot connected across the entire portfolio engine.</p>
</div>
"""
