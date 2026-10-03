"""Premium visual system for Portfolio Analytics v4."""

#______________________________________________________________________________
# THEME CSS
#______________________________________________________________________________

APP_CSS = """
<style>
:root {
    --pa-bg: #06111F;
    --pa-bg-2: #081725;
    --pa-panel: #0A1C2D;
    --pa-panel-2: #0E2438;
    --pa-panel-3: #112B42;
    --pa-border: #1C3851;
    --pa-border-soft: rgba(148,163,184,.14);
    --pa-text: #F5F0E6;
    --pa-muted: #9AAABD;
    --pa-muted-2: #718399;
    --pa-gold: #D7B768;
    --pa-gold-soft: #EBD79D;
    --pa-green: #2FD094;
    --pa-red: #FF6577;
    --pa-blue: #61A7FF;
    --pa-purple: #A78BFA;
    --pa-cyan: #46D4D8;
}

html, body, [class*="css"] {
    font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.stApp {
    background:
        radial-gradient(circle at 12% 0%, rgba(215,183,104,.08), transparent 27%),
        radial-gradient(circle at 88% 8%, rgba(97,167,255,.055), transparent 23%),
        linear-gradient(180deg, #06111F 0%, #071421 100%);
    color: var(--pa-text);
}

[data-testid="stHeader"] {
    background: rgba(6,17,31,.86);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid rgba(148,163,184,.06);
}

.block-container {
    max-width: 1540px;
    padding-top: 1.15rem;
    padding-bottom: 4rem;
}

h1, h2, h3, h4 {
    color: var(--pa-text) !important;
    letter-spacing: -.025em;
}

p, label, .stCaption, [data-testid="stCaptionContainer"] {
    color: #AAB8C7;
}

/* HERO */
.pa-hero {
    position: relative;
    overflow: hidden;
    background:
        radial-gradient(circle at 82% 0%, rgba(215,183,104,.13), transparent 27%),
        linear-gradient(135deg, rgba(17,43,66,.96), rgba(8,23,37,.98));
    border: 1px solid rgba(215,183,104,.19);
    border-radius: 22px;
    padding: 1.55rem 1.65rem;
    margin: .15rem 0 1.25rem;
    box-shadow: 0 20px 54px rgba(0,0,0,.20);
}
.pa-hero::after {
    content: "PORTFOLIO INTELLIGENCE";
    position: absolute;
    right: 1.5rem;
    top: 1.4rem;
    color: rgba(215,183,104,.34);
    font-size: .60rem;
    font-weight: 800;
    letter-spacing: .18em;
}
.pa-hero h1 {
    color: var(--pa-text) !important;
    margin: 0;
    font-size: clamp(1.9rem, 3vw, 2.45rem);
    font-weight: 790;
    letter-spacing: -.04em;
}
.pa-hero p {
    color: #AFC0D1 !important;
    margin: .55rem 0 0;
    font-size: .94rem;
    max-width: 58rem;
}
.pa-hero h1::before {
    content: "ANALYTICS • RISK • AI COPILOT";
    display: block;
    color: var(--pa-gold);
    font-size: .68rem;
    font-weight: 800;
    letter-spacing: .16em;
    margin-bottom: .5rem;
}

/* INPUTS / CONTROLS */
div[data-baseweb="select"] > div,
div[data-baseweb="input"] > div,
[data-testid="stNumberInput"] input,
textarea,
[data-testid="stTextInput"] input {
    background: rgba(14,36,56,.94) !important;
    color: var(--pa-text) !important;
    border-color: rgba(148,163,184,.16) !important;
    border-radius: 11px !important;
}

div[data-baseweb="select"] span,
[data-testid="stNumberInput"] button,
[data-testid="stTextInput"] input::placeholder,
textarea::placeholder {
    color: #91A2B5 !important;
}

[data-testid="stFileUploaderDropzone"] {
    background: rgba(14,36,56,.66) !important;
    border: 1px dashed rgba(215,183,104,.35) !important;
    border-radius: 16px !important;
}
[data-testid="stFileUploaderDropzone"]:hover {
    border-color: var(--pa-gold) !important;
    background: rgba(17,43,66,.72) !important;
}

div.stButton > button,
div.stDownloadButton > button {
    border-radius: 11px !important;
    font-weight: 700 !important;
    transition: all .18s ease;
}
div.stButton > button[kind="primary"] {
    background: linear-gradient(180deg, #E3CA84, #CFAA53) !important;
    border: 1px solid rgba(235,215,157,.72) !important;
    color: #071421 !important;
    box-shadow: 0 9px 22px rgba(215,183,104,.13);
}
div.stButton > button[kind="primary"]:hover {
    transform: translateY(-1px);
    border-color: var(--pa-gold-soft) !important;
}

/* COPILOT */
.pa-copilot {
    background:
        radial-gradient(circle at 85% 0%, rgba(215,183,104,.09), transparent 24%),
        linear-gradient(180deg, rgba(14,36,56,.96), rgba(8,23,37,.97));
    border: 1px solid rgba(215,183,104,.17);
    border-top: 3px solid var(--pa-gold);
    border-radius: 18px;
    padding: 1rem 1rem .9rem;
    box-shadow: 0 18px 42px rgba(0,0,0,.16);
}

/* TABLES */
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
    border: 1px solid var(--pa-border-soft) !important;
    border-radius: 14px !important;
    overflow: hidden;
    background: rgba(10,28,45,.92);
}

/* EXPANDERS */
div[data-testid="stExpander"] {
    border: 1px solid rgba(148,163,184,.14);
    border-radius: 13px;
    background: rgba(10,28,45,.72);
}
div[data-testid="stExpander"]:hover {
    border-color: rgba(215,183,104,.28);
}

/* CHART PANELS */
[data-testid="stPlotlyChart"] {
    background:
        linear-gradient(180deg, rgba(14,36,56,.94), rgba(8,23,37,.94)) !important;
    border: 1px solid rgba(148,163,184,.13);
    border-radius: 17px;
    padding: .35rem;
    box-shadow: 0 14px 34px rgba(0,0,0,.15);
}
[data-testid="stPlotlyChart"] > div { min-height: 0 !important; }

/* INSIGHT CALLOUTS */
.pa-insight {
    background: rgba(14,36,56,.72);
    border: 1px solid rgba(148,163,184,.11);
    border-left: 3px solid var(--pa-gold);
    border-radius: 10px;
    padding: .72rem .85rem;
    margin: .42rem 0 1rem;
    color: #B7C5D3;
    font-size: .86rem;
    line-height: 1.52;
}
.pa-small { color: var(--pa-muted); font-size: .82rem; }
.pa-section-note { color: var(--pa-muted); font-size: .86rem; margin-top: -.4rem; margin-bottom: .8rem; }
.pa-scenario-banner {
    background: rgba(215,183,104,.08);
    border: 1px solid rgba(215,183,104,.24);
    border-left: 4px solid var(--pa-gold);
    border-radius: 11px;
    padding: .75rem .9rem;
    margin: .4rem 0 1rem;
    color: #D6E0E9;
}

/* MAJOR SECTION HEADERS */
.pa-section-header {
    position: relative;
    overflow: hidden;
    margin: 2rem 0 .95rem;
    padding: 17px 20px 16px;
    background:
        radial-gradient(circle at 91% 10%, rgba(215,183,104,.10), transparent 26%),
        linear-gradient(110deg, rgba(17,43,66,.93), rgba(10,28,45,.96));
    border: 1px solid rgba(215,183,104,.16);
    border-radius: 16px;
    box-shadow: 0 14px 38px rgba(0,0,0,.13);
}
.pa-section-header::before {
    content: "";
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 3px;
    background: linear-gradient(180deg, #E7CD88, #B98E39);
}
.pa-section-header-eyebrow {
    color: var(--pa-gold);
    font-size: .62rem;
    font-weight: 820;
    letter-spacing: .17em;
    text-transform: uppercase;
    margin-bottom: 4px;
}
.pa-section-header-title {
    color: var(--pa-text);
    font-size: clamp(1.12rem, 1.65vw, 1.38rem);
    line-height: 1.2;
    font-weight: 760;
    letter-spacing: -.02em;
}
.pa-section-header-note {
    color: #99AABD;
    font-size: .76rem;
    line-height: 1.48;
    margin-top: 6px;
    max-width: 930px;
}

/* KPI STRIPS */
.pa-kpi-group-label {
    color: var(--pa-gold);
    font-size: .64rem;
    font-weight: 800;
    letter-spacing: .15em;
    margin: .82rem 0 .42rem;
}
.pa-finance-kpi {
    min-height: 84px;
    padding: 8px 12px 10px 0;
    border-bottom: 2px solid rgba(148,163,184,.16);
    position: relative;
}
.pa-finance-kpi::after {
    content: "";
    position: absolute;
    right: 0;
    top: 10px;
    bottom: 15px;
    width: 1px;
    background: rgba(148,163,184,.10);
}
.pa-finance-kpi-label {
    color: #91A2B5;
    font-size: .75rem;
    font-weight: 650;
    margin-bottom: 4px;
}
.pa-finance-kpi-value {
    color: var(--pa-text);
    font-size: clamp(1.24rem, 1.9vw, 1.7rem);
    line-height: 1.08;
    font-weight: 770;
    letter-spacing: -.035em;
    white-space: nowrap;
}
.pa-finance-kpi-exact {
    color: #708399;
    font-size: .68rem;
    margin-top: 6px;
    white-space: nowrap;
}
.pa-finance-kpi[data-tone="positive"] { border-bottom-color: rgba(47,208,148,.54); }
.pa-finance-kpi[data-tone="positive"] .pa-finance-kpi-value { color: var(--pa-green); }
.pa-finance-kpi[data-tone="risk"] { border-bottom-color: rgba(255,101,119,.52); }
.pa-finance-kpi[data-tone="risk"] .pa-finance-kpi-value { color: var(--pa-red); }
.pa-finance-kpi[data-tone="liquidity"] { border-bottom-color: rgba(97,167,255,.52); }
.pa-finance-kpi[data-tone="liquidity"] .pa-finance-kpi-value { color: var(--pa-blue); }
.pa-finance-kpi[data-tone="exposure"] { border-bottom-color: rgba(167,139,250,.52); }
.pa-finance-kpi[data-tone="exposure"] .pa-finance-kpi-value { color: var(--pa-purple); }

.pa-inline-stat {
    min-height: 64px;
    padding: 6px 10px 9px 0;
    border-bottom: 2px solid rgba(148,163,184,.16);
}
.pa-inline-stat-label {
    color: #91A2B5;
    font-size: .72rem;
    font-weight: 650;
    margin-bottom: 3px;
}
.pa-inline-stat-value {
    color: var(--pa-text);
    font-size: clamp(1.02rem, 1.5vw, 1.36rem);
    line-height: 1.1;
    font-weight: 760;
    letter-spacing: -.025em;
    white-space: nowrap;
}
.pa-inline-stat-detail {
    color: #718399;
    font-size: .64rem;
    line-height: 1.35;
    margin-top: 5px;
}
.pa-inline-stat[data-tone="positive"] { border-bottom-color: rgba(47,208,148,.54); }
.pa-inline-stat[data-tone="positive"] .pa-inline-stat-value { color: var(--pa-green); }
.pa-inline-stat[data-tone="risk"] { border-bottom-color: rgba(255,101,119,.52); }
.pa-inline-stat[data-tone="risk"] .pa-inline-stat-value { color: var(--pa-red); }
.pa-inline-stat[data-tone="gold"] { border-bottom-color: rgba(215,183,104,.64); }
.pa-inline-stat[data-tone="gold"] .pa-inline-stat-value { color: var(--pa-gold-soft); }

/* LEGACY METRICS: FLAT, NEVER GIANT CARDS */
[data-testid="stMetric"] {
    background: transparent;
    border: 0;
    border-bottom: 2px solid rgba(215,183,104,.34);
    border-radius: 0;
    padding: 5px 8px 9px 0;
    box-shadow: none;
    min-height: 64px;
    overflow: visible;
}
[data-testid="stMetricLabel"] {
    color: #91A2B5;
    font-weight: 650;
    font-size: .74rem;
}
[data-testid="stMetricValue"] {
    color: var(--pa-text);
    font-weight: 760;
    font-size: clamp(1.05rem, 1.55vw, 1.4rem);
}

/* RADIO / TOGGLES / TABS */
[data-testid="stRadio"] label,
[data-testid="stCheckbox"] label,
[data-testid="stToggle"] label {
    color: #B9C5D1 !important;
}

/* ALERTS */
[data-testid="stAlert"] {
    border-radius: 12px;
    background: rgba(14,36,56,.84);
    border: 1px solid rgba(148,163,184,.14);
}

/* SCROLLBAR */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: #071421; }
::-webkit-scrollbar-thumb { background: #20384F; border-radius: 999px; }
::-webkit-scrollbar-thumb:hover { background: #2A4966; }

@media (max-width: 900px) {
    .block-container { padding-left: .85rem; padding-right: .85rem; }
    .pa-finance-kpi { min-height: 78px; }
    .pa-finance-kpi-value { font-size: 1.18rem; }
    .pa-hero { padding: 1.25rem; }
    .pa-section-header { padding: 14px 16px; }
}
</style>
"""

HERO_HTML = """
<div class="pa-hero">
  <h1>Portfolio Analytics</h1>
  <p>Institutional-style portfolio accounting, historical risk analytics and scenario intelligence with an AI Copilot connected across the engine.</p>
</div>
"""
