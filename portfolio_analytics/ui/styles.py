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
    --pa-green: #16835D;
    --pa-green-soft: #EAF6F0;
    --pa-red: #C84A54;
    --pa-red-soft: #FCEDEF;
    --pa-blue: #2D6FA3;
    --pa-blue-soft: #EDF5FB;
    --pa-purple: #7557A8;
    --pa-purple-soft: #F3EFF9;
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
    background: transparent;
    border: 0;
    border-bottom: 2px solid rgba(201,154,53,.55);
    border-radius: 0;
    padding: 5px 8px 9px 0;
    box-shadow: none;
    min-height: 68px;
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

.pa-kpi-group-label {
    color: #8A7040;
    font-size: .68rem;
    font-weight: 760;
    letter-spacing: .14em;
    margin: .55rem 0 .35rem;
}
.pa-finance-kpi {
    min-height: 88px;
    padding: 7px 12px 10px 0;
    border-bottom: 1px solid #DEDAD1;
    position: relative;
}
.pa-finance-kpi::after {
    content: "";
    position: absolute;
    right: 0;
    top: 10px;
    bottom: 16px;
    width: 1px;
    background: #EEEAE2;
}
.pa-finance-kpi-label {
    color: #6D7785;
    font-size: .78rem;
    font-weight: 650;
    letter-spacing: .01em;
    margin-bottom: 4px;
}
.pa-finance-kpi-value {
    color: var(--pa-navy);
    font-size: clamp(1.32rem, 2vw, 1.85rem);
    line-height: 1.08;
    font-weight: 760;
    letter-spacing: -.035em;
    white-space: nowrap;
}
.pa-finance-kpi-exact {
    color: #7B8490;
    font-size: .70rem;
    margin-top: 6px;
    white-space: nowrap;
}
.pa-finance-kpi[data-tone="positive"] { border-bottom-color: rgba(22,131,93,.55); }
.pa-finance-kpi[data-tone="positive"] .pa-finance-kpi-value { color: var(--pa-green); }
.pa-finance-kpi[data-tone="risk"] { border-bottom-color: rgba(200,74,84,.52); }
.pa-finance-kpi[data-tone="risk"] .pa-finance-kpi-value { color: var(--pa-red); }
.pa-finance-kpi[data-tone="liquidity"] { border-bottom-color: rgba(45,111,163,.50); }
.pa-finance-kpi[data-tone="liquidity"] .pa-finance-kpi-value { color: var(--pa-blue); }
.pa-finance-kpi[data-tone="exposure"] { border-bottom-color: rgba(117,87,168,.48); }
.pa-finance-kpi[data-tone="exposure"] .pa-finance-kpi-value { color: var(--pa-purple); }



.pa-inline-stat {
    min-height: 68px;
    padding: 5px 10px 9px 0;
    border-bottom: 2px solid #DDD8CF;
}
.pa-inline-stat-label {
    color: #697587;
    font-size: .74rem;
    font-weight: 650;
    margin-bottom: 3px;
}
.pa-inline-stat-value {
    color: var(--pa-navy);
    font-size: clamp(1.08rem, 1.65vw, 1.45rem);
    line-height: 1.1;
    font-weight: 760;
    letter-spacing: -.025em;
    white-space: nowrap;
}
.pa-inline-stat-detail {
    color: #7B8490;
    font-size: .66rem;
    line-height: 1.35;
    margin-top: 5px;
}
.pa-inline-stat[data-tone="positive"] { border-bottom-color: rgba(22,131,93,.58); }
.pa-inline-stat[data-tone="positive"] .pa-inline-stat-value { color: var(--pa-green); }
.pa-inline-stat[data-tone="risk"] { border-bottom-color: rgba(200,74,84,.56); }
.pa-inline-stat[data-tone="risk"] .pa-inline-stat-value { color: var(--pa-red); }
.pa-inline-stat[data-tone="gold"] { border-bottom-color: rgba(201,154,53,.72); }
.pa-inline-stat[data-tone="gold"] .pa-inline-stat-value { color: #8A6722; }

.pa-section-header {
    margin: 1.55rem 0 .85rem;
    padding: 15px 18px 14px;
    background: linear-gradient(100deg, #0A3153 0%, #123F64 100%);
    border-left: 4px solid var(--pa-gold);
    border-radius: 10px;
    box-shadow: 0 5px 16px rgba(8,43,76,.08);
}
.pa-section-header-eyebrow {
    color: #E2B95F;
    font-size: .64rem;
    font-weight: 780;
    letter-spacing: .15em;
    text-transform: uppercase;
    margin-bottom: 3px;
}
.pa-section-header-title {
    color: #FFFFFF;
    font-size: 1.18rem;
    line-height: 1.2;
    font-weight: 730;
    letter-spacing: -.015em;
}
.pa-section-header-note {
    color: #CAD6E0;
    font-size: .78rem;
    line-height: 1.45;
    margin-top: 5px;
    max-width: 1000px;
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
