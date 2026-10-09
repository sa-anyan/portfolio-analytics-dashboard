import sys,json,math,statistics,platform,argparse,importlib.metadata as md
from pathlib import Path
from decimal import Decimal as D
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_math_regressions import state,DATES
from tests.test_consolidation import maya_reviewed,james_reviewed,james_fx,JAMES_QUOTES,declarations
from tests.test_lookthrough import fixture,calc,nvda
from tests.test_constituents import draft,DATE
from portfolio_analytics.input_engine.consolidate import prepare_consolidation
from portfolio_analytics.input_engine.constituents import accept_constituents
from portfolio_analytics.analytics.engine import _series_metrics,_risk_contribution,run_analytics
from portfolio_analytics.scenarios.engine import run_scenario
from portfolio_analytics.diagnostics.trust import diagnose_trust
parser=argparse.ArgumentParser()
parser.add_argument('--output',default='audit-results.json')
args=parser.parse_args()
rows=[]
def check(name,expected,actual,tol=1e-9):
    passed=actual is None if expected is None else actual is not None and abs(float(actual)-float(expected))<=tol
    rows.append(dict(check=name,expected=expected,actual=actual,absolute_tolerance=tol,passed=passed))
fx=pd.DataFrame({'GBP':[1.25,1.3,1.35,1.4]},index=DATES)
s=state({'Ticker':['UK','US','CASH'],'Quantity':[100,1,100],'Currency':['GBX','USD','GBP'],'Current Price':[200,100,1]},fx_history=fx)
for key,val in {'equity':520,'cash':140,'long_exposure':380,'short_exposure':0,'gross_exposure':380,'net_exposure':380}.items():check('GBX mixed '+key,val,s['totals'][key])
check('200 GBX × 1.4 GBP/USD /100',2.8,s['positions'][0]['current_price'])
a=run_analytics(s,pd.DataFrame({'UK':[200]*4,'US':[100]*4},index=DATES),fx_history=fx)
scenario=run_scenario(s,a,[{'action':'resize','ticker':'UK','target_quantity':50}])
check('Self-financing resize equity',520,scenario['scenario_state']['totals']['equity']);check('Resize proceeds USD',140,scenario['scenario_state']['cash']['balances']['USD'])
# Average cost independent hand ledger: 10@5 + 10@7; sell5@10; remaining15@6 marked8.
ledger=state({'Date':['2025-01-02']*4,'Type':['DEPOSIT','BUY','BUY','SELL'],'Ticker':[None,'X','X','X'],'Quantity':[None,10,10,5],'Price':[None,5,7,10],'Amount':[200,None,None,None],'Currency':['USD']*4},latest_prices={'X':8})
for k,v in {'equity':250,'cash':130,'cost_basis':90,'realised_pnl':20,'unrealised_pnl':30}.items():check('Average-cost ledger '+k,v,ledger['totals'][k])
check('Average cost per remaining share',6,ledger['positions'][0]['average_entry_price'])
m=prepare_consolidation(maya_reviewed(),declarations=declarations())
for k,v in {'equity':4050,'cash':350,'signed_market_value':3700,'cost_basis':2610,'unrealised_pnl':1090,'realised_pnl':None}.items():check('Maya '+k,v,m['state']['totals'][k])
j=prepare_consolidation(james_reviewed(),declarations=declarations(['Broker']),latest_prices=JAMES_QUOTES,fx_history=james_fx())
for k,v in {'cash':163,'equity':152.4,'signed_market_value':-10.6,'gross_exposure':79.4,'long_exposure':34.4,'short_exposure':45,'net_exposure':-10.6,'cost_basis':77.8,'unrealised_pnl':11.6,'realised_pnl':None}.items():check('James '+k,v,j['state']['totals'][k])
returns=[-.1,.05,-.02,.03];metrics=_series_metrics(pd.Series(returns))
# Independent order-statistic interpolation: 5th percentile = -0.1+0.15*(0.08)=-.088.
for k,v in {'max_drawdown':-.1,'annual_volatility':statistics.stdev(returns)*math.sqrt(252),'var_pct':.088,'expected_shortfall_pct':.1}.items():check('Risk oracle '+k,v,metrics[k],1e-12)
correlated=pd.DataFrame({'A':returns,'B':[2*x for x in returns]});rc=_risk_contribution(correlated,pd.Series({'A':.6,'B':.4}))
check('Perfectly correlated A signed risk share',3/7,rc[0]['risk_contribution_pct'],1e-12)
check('Perfectly correlated B signed risk share',4/7,rc[1]['risk_contribution_pct'],1e-12)
check('Risk shares sum',1,sum(x['risk_contribution_pct'] for x in rc),1e-12)
empty=_series_metrics(pd.Series([],dtype=float));check('Missing risk is unavailable',None,empty['annual_volatility'])
s,p,c,store,decl=fixture();r=calc(s,c,store,decl);n=nvda(r)
for k,v in {'direct':1000,'known_total':1722.5}.items():check('Daniel '+k,v,n[k])
check('Daniel VOO exposure',162.5,n['indirect_by_fund']['VOO']);check('Daniel VGT exposure',560,n['indirect_by_fund']['VGT']);check('Daniel equity share',1722.5/6300,n['equity_fraction'],1e-12)
store=accept_constituents(store,draft('synthetic_voo_top_ten.csv'),c,decisions={'completeness':'partial'},as_of=DATE);r=calc(s,c,store,decl)
check('53 percent VOO unknown dollars',1175,r['funds'][0]['unknown_exposure']);check('Partial decomposition residual',0,r['reconciliation']['residual'])
# Semantic challenge: snapshot has no disposed-position history. This is not an arithmetic claim.
check('Snapshot realised P&L must be unavailable without history',None,s['totals']['realised_pnl'])
blocked=prepare_consolidation(james_reviewed(),declarations=declarations(['Broker']),latest_prices={k:v for k,v in JAMES_QUOTES.items() if k!='UNPRICED'},fx_history=james_fx())
result={'baseline':'e085d7a','environment':{'python':sys.version,'platform':platform.platform(),'packages':{p:md.version(p) for p in ['streamlit','pandas','numpy','plotly','yfinance','openpyxl','openai','pytest']}},'numerical_checks':rows,'maya_reconciliation':m['reconciliation'],'james_reconciliation':j['reconciliation'],'unpriced_blocked':not blocked['ready'],'unpriced_diagnostics':blocked['trust']['issues']}
Path(args.output).write_text(json.dumps(result,indent=2,default=str))
print(json.dumps({'checks':len(rows),'passed':sum(x['passed'] for x in rows),'discrepancies':[x for x in rows if not x['passed']]},indent=2))

if not all(row['passed'] for row in rows): raise SystemExit(1)
