"""Read-only capital-allocation decomposition of canonical market values.

No pricing, FX requests, accounting, recursive funds or risk model live here.
"""
from decimal import Decimal, InvalidOperation, localcontext
from copy import deepcopy
from datetime import date

from portfolio_analytics.input_engine.constituents import saved_coverage, validate_constituents


def number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def is_fund(item):
    return any(word in str(item.get('asset_class', '')).lower() for word in ('etf', 'fund', 'trust'))


def eligibility_declaration(item, reason):
    """Bind a user attestation to the exact current parent identity."""
    return {'ordinary_equity_capital_allocation': True, 'reason': reason.strip(),
            'parent_identity': deepcopy(item)}


def calculate_lookthrough(state, store, catalog, *, declarations=None, trust=None,
                          as_of=None, max_age_days=90):
    with localcontext() as ctx:
        ctx.prec = 128
        return _calculate(state, store, catalog, declarations or {}, trust or {},
                          as_of or date.today().isoformat(), max_age_days)


def _calculate(state, store, catalog, declarations, trust, as_of, maximum):
    equity = number(state.get('totals', {}).get('equity'))
    valid = equity is not None and trust.get('valuation_status') != 'blocked'
    positions = state.get('positions', [])
    valid = valid and all(number(p.get('signed_market_value')) is not None for p in positions)
    result = {'reporting_currency': state.get('meta', {}).get('base_currency', 'USD'),
              'valuation_valid': bool(valid), 'equity': str(equity) if valid else None,
              'securities': [], 'funds': [], 'findings': [], 'issues': [],
              'methodology': 'Canonical fund market value × validated original capital weight. Decomposition only; owned equity is unchanged. Listing currencies do not establish economic currency exposure. Security/share-class identities are not issuer-level company aggregation.'}
    entries, eligible, unknown = [], Decimal(0), Decimal(0)
    complete = True
    positive = all((number(p.get('signed_market_value')) or Decimal(0)) >= 0 for p in positions)

    def issue(code, explanation, parent=None):
        result['issues'].append({'code': code, 'parent': parent, 'explanation': explanation})

    for p in positions:
        ticker = p['ticker']
        item = catalog.get(ticker, {})
        value = number(p.get('signed_market_value')) if valid else None
        if not is_fund(p) and ticker not in store:
            if str(p.get('asset_class', '')).lower() in ('equity', 'stock') and value is not None:
                entries.append({'ids': item.get('identifiers', {'ticker': [ticker]}), 'name': p.get('asset_name', ticker),
                                'direct': value, 'amount': value, 'parent': None, 'ticker': ticker, 'evidence': {'identity_status': 'canonical direct holding'}})
                eligible += value
            else:
                complete = False
                issue('outside_scope', 'Direct non-equity or unvalued holding is outside the equity decomposition.', ticker)
            continue
        fund = {'parent': ticker, 'market_value': str(value) if value is not None else None,
                'status': 'unavailable', 'known_exposure': None, 'unknown_exposure': None}
        result['funds'].append(fund)
        declaration = declarations.get(ticker, {})
        unsupported = any(w in (str(p.get('asset_name', ''))+' '+str(p.get('asset_class', ''))).lower()
                          for w in ('leveraged', 'inverse', 'derivative', 'synthetic', '2x', '3x'))
        if value is None or value <= 0 or p.get('side') == 'SHORT' or unsupported:
            issue('unsupported_fund', 'Fund valuation is unavailable, position is not long, or its structure is unsupported.', ticker)
            complete = False
            continue
        if not (declaration.get('ordinary_equity_capital_allocation') and declaration.get('reason', '').strip()
                and declaration.get('parent_identity') == item):
            issue('eligibility_unverified', 'Confirm ordinary long-only equity structure and whole-fund capital-allocation weights before applying look-through.', ticker)
            complete = False
            continue
        snapshot = store.get(ticker)
        eligible += value
        if not snapshot:
            fund.update(status='no data', unknown_exposure=str(value))
            unknown += value
            complete = False
            issue('no_data', 'No constituent data; full fund value is unknown underlying exposure.', ticker)
            continue
        coverage = saved_coverage(snapshot, catalog, as_of=as_of, max_age_days=maximum)
        fund['coverage'] = coverage
        draft = {'files': snapshot['sources'], 'records': snapshot['raw_records'],
                 'selected_parent': ticker, 'fingerprint': 'accepted-read-only-revalidation'}
        checked = validate_constituents(draft, catalog, decisions=snapshot['decisions'], as_of=as_of, max_age_days=maximum)
        records = checked['records']
        if not coverage['parent_current'] or not checked['ready'] or any(str(r.get('asset_class', '')).lower() not in ('equity', 'stock') for r in records):
            fund.update(status='unsupported or unresolved composition', unknown_exposure=str(value))
            unknown += value
            complete = False
            issue('composition_unavailable', 'Parent/mapping no longer validates, or composition contains cash, non-equity, nested funds or unresolved instrument types. No derivative exposure inferred.', ticker)
            continue
        subtotal = sum((Decimal(r['weight']) for r in records), Decimal(0))
        residual = value * (1-subtotal)
        fund.update(status=coverage['completeness'], known_exposure=str(value*subtotal),
                    unknown_exposure=str(residual) if coverage['unreported_weight'] is not None else None,
                    arithmetic_residual=str(residual))
        unknown += residual
        if coverage['completeness'] != 'reported_complete':
            complete = False
            issue('unverified_composition' if coverage['completeness'] == 'unverified' else 'incomplete_coverage', 'Only original reported weights are applied. Unreported holdings may include a known security; 100% reported weight without completeness verification remains unverified.', ticker)
        if coverage['stale']:
            complete = False
            issue('stale', 'Constituent observations exceed the configured calendar-day rule or their date is unverifiable; amounts are historical-composition estimates.', ticker)
        for r in records:
            entries.append({'ids': r['resolved_identifiers'], 'name': r['name'], 'direct': Decimal(0),
                            'amount': value*Decimal(r['weight']), 'parent': ticker, 'ticker': None,
                            'evidence': {key: r.get(key) for key in ('record_id', 'source_file', 'source_fingerprint', 'weight', 'as_of', 'source', 'identity_status', 'identifiers', 'resolved_identifiers', 'parent_mapping')}})
    dates = {f['coverage']['as_of'] for f in result['funds'] if f.get('coverage', {}).get('as_of')}
    if len(dates) > 1:
        complete = False
        issue('different_dates', 'Fund constituent snapshots have different dates; the combined view is not a common-date composition.')
    # Connected typed-ID components; any contradictory typed identifier makes the
    # entire component unresolved rather than silently merging share classes.
    roots = list(range(len(entries)))
    def root(i):
        while roots[i] != i:
            roots[i] = roots[roots[i]]
            i = roots[i]
        return i
    seen = {}
    for i, entry in enumerate(entries):
        for kind, values in entry['ids'].items():
            for value in values:
                key = (kind, value)
                if key in seen:
                    roots[root(i)] = root(seen[key])
                else:
                    seen[key] = i
    components = {}
    for i in range(len(entries)):
        components.setdefault(root(i), []).append(entries[i])
    unresolved = Decimal(0)
    for rows in components.values():
        merged = {}
        for row in rows:
            for kind, values in row['ids'].items():
                merged.setdefault(kind, set()).update(values)
        conflict = not any(merged.values()) or any(len(v) > 1 for v in merged.values())
        if conflict:
            unresolved += sum((r['amount'] for r in rows), Decimal(0))
            complete = False
            issue('identity_conflict', 'Conflicting typed identifiers prevent aggregation. Affected amounts remain unresolved: '+', '.join(r['name'] for r in rows))
            continue
        direct = sum((r['direct'] for r in rows), Decimal(0))
        parents = sorted({r['parent'] for r in rows if r['parent']})
        by_fund = {p: str(sum((r['amount'] for r in rows if r['parent'] == p), Decimal(0))) for p in parents}
        total = sum((r['amount'] for r in rows), Decimal(0))
        security = {'name': rows[0]['name'], 'identifiers': {k: sorted(v) for k,v in merged.items()},
                    'direct': str(direct), 'indirect_by_fund': by_fund, 'known_total': str(total),
                    'equity_fraction': str(total/equity) if valid and equity > 0 else None,
                    'evidence': [r['evidence'] for r in rows]}
        result['securities'].append(security)
        if len(parents) > 1:
            result['findings'].append({'code': 'overlap', 'security': security['name'], 'explanation': 'The same verified security appears in '+', '.join(parents)+'.', 'known_total': str(total)})
        if direct and parents:
            result['findings'].append({'code': 'direct_indirect', 'security': security['name'], 'explanation': 'Direct ownership and fund constituents contribute to the same security exposure; they are decomposed, not added to portfolio equity.', 'known_total': str(total)})
        if security['equity_fraction'] is not None and total/equity >= Decimal('0.20'):
            result['findings'].append({'code': 'concentration', 'security': security['name'], 'explanation': 'Known exposure exceeds the explicit 20% of equity review threshold; this is not an investment recommendation.', 'known_total': str(total), 'equity_fraction': security['equity_fraction']})
    result['securities'].sort(key=lambda s: (-Decimal(s['known_total']), s['name']))
    known = sum((Decimal(s['known_total']) for s in result['securities']), Decimal(0))
    result['complete_ranking'] = complete and bool(valid)
    result['known_is_lower_bound'] = positive and bool(valid) and not any(i['code'] in ('unsupported_fund', 'eligibility_unverified', 'composition_unavailable', 'stale', 'different_dates', 'identity_conflict', 'unverified_composition') for i in result['issues'])
    result['reconciliation'] = {'eligible_total': str(eligible) if valid else None, 'known_total': str(known) if valid else None,
                                'unknown_residual': str(unknown) if valid else None, 'unresolved_identity_amount': str(unresolved) if valid else None,
                                'residual': str(eligible-known-unknown-unresolved) if valid else None,
                                'scope': 'Eligible funds and direct equities only; cash and unsupported holdings excluded. Arithmetic residual for unverified 100% datasets does not verify composition.'}
    if not valid:
        issue('invalid_valuation', 'Canonical valuation or Trust diagnostics is invalid; monetary exposure and equity percentages are unavailable.')
    result['findings'] += result['issues']
    return result
