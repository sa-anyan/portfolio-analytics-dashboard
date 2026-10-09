"""Supplementary fund snapshot metadata. Never account for owned positions.

Weights are explicit fund fractions, not portfolio exposure or risk. Names are
not identity keys; no provider requests or financial engine calls occur here.
"""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import re

import pandas as pd

from .parser import read_portfolio_file
from .review import _hash


def token(value):
    return '' if value is None or pd.isna(value) else str(value).strip().upper()


def header(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


ALIASES = {
    'parent_ticker': {'parentticker', 'fundticker', 'etfticker', 'parentfund', 'fund', 'etf'},
    'parent_isin': {'parentisin', 'fundisin', 'etfisin'},
    'parent_cusip': {'parentcusip', 'fundcusip'},
    'parent_sedol': {'parentsedol', 'fundsedol'},
    'parent_id': {'parentidentifier', 'parentid', 'fundidentifier', 'fundid'},
    'ticker': {'constituentticker', 'underlyingticker', 'ticker', 'symbol', 'companyticker'},
    'isin': {'constituentisin', 'underlyingisin', 'isin', 'companyisin'},
    'cusip': {'constituentcusip', 'underlyingcusip', 'cusip'},
    'sedol': {'constituentsedol', 'underlyingsedol', 'sedol'},
    'security_id': {'constituentidentifier', 'underlyingidentifier', 'securityid', 'constituentid', 'underlyingid', 'companyid', 'companyidentifier'},
    'name': {'constituentname', 'underlyingname', 'securityname', 'name', 'companyname'},
    'weight': {'weight', 'constituentweight', 'portfolioweight', 'fundweight'},
    'weight_format': {'weightformat', 'weightunit', 'weightunits'},
    'as_of': {'asof', 'asofdate', 'holdingsdate', 'snapshotdate', 'valuationdate'},
    'source': {'source', 'datasource', 'provider', 'sourceurl'},
    'asset_class': {'assetclass', 'instrumenttype', 'securitytype'},
    'currency': {'currency', 'ccy', 'quotecurrency'},
}


def identity_catalog(state, *, parsed=None, accepted_batch=None):
    """Exact identifiers from current positions and retained source partitions."""
    catalog = {p['ticker']: {'ticker': p['ticker'], 'name': str(p.get('asset_name') or ''),
               'asset_class': str(p.get('asset_class') or ''), 'identifiers': {'ticker': [p['ticker']], 'isin': [], 'security_id': [], 'cusip': [], 'sedol': []}}
               for p in (state or {}).get('positions', [])}
    sources = [(parsed or {}).get('user_dataset', {}).get('records', [])]
    if accepted_batch:
        pids = {r['part_id'] for r in (state or {}).get('consolidation', {}).get('records', [])}
        sources += [p['parsed']['user_dataset']['records'] for f in accepted_batch['files'] for p in f['parts'] if p['part_id'] in pids]
    for rows in sources:
        for row in rows:
            clean = {header(k): v for k, v in row.items() if k != '_provenance'}
            ticker = token(next((clean[k] for k in ('ticker', 'symbol', 'security', 'instrumentcode', 'assetticker') if k in clean), ''))
            if ticker not in catalog:
                continue
            for kind, keys in {'isin': ('isin', 'securityisin'), 'security_id': ('securityid', 'instrumentid'), 'cusip': ('cusip',), 'sedol': ('sedol',)}.items():
                for key in keys:
                    if token(clean.get(key)) and token(clean[key]) not in catalog[ticker]['identifiers'][kind]:
                        catalog[ticker]['identifiers'][kind].append(token(clean[key]))
    return catalog


def matches(ids, catalog):
    return sorted(t for t, item in catalog.items() if any(
        set(values) & set(item['identifiers'].get(kind, [])) for kind, values in ids.items()))


def contradicts(ids, item):
    return any((set(values)-{''}) and item['identifiers'].get(kind) and not (set(values)-{''}) & set(item['identifiers'][kind])
               for kind, values in ids.items())


def stage_constituents(files, *, selected_parent):
    from portfolio_analytics.security.uploads import validate_batch
    validate_batch(files)
    draft = {'selected_parent': selected_parent, 'files': [], 'records': []}
    for n, (filename, data) in enumerate(files, 1):
        fingerprint = sha256(data).hexdigest()
        sid = f'{n}:{fingerprint[:16]}'
        source = {'source_id': sid, 'filename': filename, 'fingerprint': fingerprint, 'errors': [], 'raw_records': []}
        draft['files'].append(source)
        try:
            # Underlying fund composition can contain far more securities than an owned book.
            frame = read_portfolio_file(data, filename, dtype=str, keep_default_na=False, security_limit=20_000)
            if frame.empty:
                raise ValueError('Constituent file is empty.')
            mapping = {}
            for field, aliases in ALIASES.items():
                columns = [c for c in frame if header(re.sub(r'\.\d+$', '', str(c))) in aliases]
                if len(columns) > 1:
                    raise ValueError(f'Ambiguous {field} columns: {columns}. Retain one authoritative column.')
                if columns:
                    mapping[field] = columns[0]
            if 'weight' not in mapping or 'as_of' not in mapping:
                raise ValueError('Constituent files require Weight and As Of Date columns.')
            source['raw_records'] = frame.to_dict('records')
            for ordinal, raw in enumerate(source['raw_records'], 2):
                fields = {field: str(raw[column]).strip() for field, column in mapping.items()}
                date = _date(fields.get('as_of'))
                draft['records'].append({'record_id': f'{sid}:{ordinal}', 'source_id': sid, 'source_file': filename,
                    'source_fingerprint': fingerprint, 'source_row': ordinal, 'raw': deepcopy(raw), 'fields': fields,
                    'snapshot_id': f'{sid}@{date or "invalid-date"}'})
        except Exception as exc:
            source['errors'].append(str(exc))
    draft['fingerprint'] = _hash(draft)
    return draft


def _date(value):
    if not value or str(value).strip().isdigit():
        return None
    # ISO avoids silently choosing US vs European ambiguous numeric date order.
    try:
        date = pd.Timestamp(value)
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[ T].*)?', str(value).strip()):
            return None
        return date.date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _weight(value, unit):
    value, unit = str(value or '').strip(), str(unit or '').strip().lower()
    if value.endswith('%') and not unit:
        unit = 'percentage'
    if unit in {'percent', 'percentage', 'pct', '%'}:
        unit = 'percentage'
    elif unit in {'decimal', 'fraction'}:
        unit = 'decimal'
    else:
        return None, 'Declare percentage or decimal weight format; bare numbers are not guessed.'
    if not value:
        return None, 'Constituent weight is missing.'
    if value.endswith('%') and unit != 'percentage':
        return None, 'Percent suffix conflicts with decimal format.'
    try:
        number = Decimal(value.removesuffix('%').strip())
    except InvalidOperation:
        return None, 'Constituent weight is invalid.'
    if not number.is_finite():
        return None, 'Constituent weight must be finite.'
    if number < 0 or number > (100 if unit == 'percentage' else 1):
        return None, 'Constituent weight must be between 0% and 100%.'
    parts = number.as_tuple()
    weight = Decimal((parts.sign, parts.digits, parts.exponent-2)) if unit == 'percentage' else number
    if weight.as_tuple().exponent < -64:
        return None, 'Weight precision exceeds the supported 64 fractional decimal places; the original value is retained for correction.'
    if weight < 0 or weight > 1:
        return None, 'Constituent weight must be between 0% and 100%.'
    return weight, None


def validate_constituents(draft, catalog, *, decisions=None, as_of=None, max_age_days=90):
    decisions = deepcopy(decisions or {})
    today = pd.Timestamp(as_of or pd.Timestamp.today()).date()
    if isinstance(max_age_days, bool) or not isinstance(max_age_days, int) or max_age_days < 0:
        raise ValueError('Freshness rule must be a nonnegative integer of calendar days.')
    parent = draft['selected_parent']
    issues, accepted = [], []
    def issue(code, severity, explanation, ids=()):
        issues.append({'code': code, 'severity': severity, 'explanation': explanation, 'record_ids': sorted(ids)})
    if parent not in catalog:
        issue('parent_not_held', 'blocker', 'Selected parent is absent from the accepted portfolio.')
    excluded_sources = decisions.get('excluded_sources', {})
    source_ids = {f['source_id'] for f in draft['files']}
    if set(excluded_sources) - source_ids or any(not str(reason).strip() for reason in excluded_sources.values()):
        issue('source_exclusion_invalid', 'blocker', 'Every source exclusion requires a known file and reason.')
    for source in draft['files']:
        if source['source_id'] in excluded_sources:
            continue
        for error in source['errors']:
            issue('source_invalid', 'blocker', f"{source['filename']}: {error}")
    snapshots = sorted({r['snapshot_id'] for r in draft['records'] if r['source_id'] not in excluded_sources})
    selected = decisions.get('snapshot_id')
    if len(snapshots) == 1:
        selected = snapshots[0]
    elif len(snapshots) > 1 and (selected not in snapshots or not str(decisions.get('snapshot_reason', '')).strip()):
        issue('snapshot_conflict', 'blocker', 'Select exactly one source/date snapshot with a reason; holdings dates/files are never combined automatically.')
    elif not snapshots:
        issue('no_records', 'blocker', 'No constituent records are available.')
    excluded = decisions.get('excluded_records', {})
    known = {r['record_id'] for r in draft['records']}
    if set(decisions.get('identity_mappings', {})) - known:
        issue('identity_decision_invalid', 'blocker', 'Identity decisions reference records outside this draft.')
    if set(excluded) - known or any(not str(reason).strip() for reason in excluded.values()):
        issue('exclusion_invalid', 'blocker', 'Every exclusion requires an affected record and explicit reason.')
    if parent in catalog and not any(word in catalog[parent]['asset_class'].lower() for word in ('etf', 'fund', 'trust')):
        if not decisions.get('fund_confirmed') or not str(decisions.get('fund_reason', '')).strip():
            issue('fund_type_unverified', 'blocker', 'Confirm that the selected security is a fund/ETF; the canonical holding is not automatically reclassified.')
    for record in draft['records']:
        if record['snapshot_id'] != selected or record['record_id'] in excluded or record['source_id'] in excluded_sources:
            continue
        fields, rid = record['fields'], record['record_id']
        date = _date(fields.get('as_of'))
        if not date or pd.Timestamp(date).date() > today:
            issue('date_invalid', 'blocker', 'Holdings date must be a valid non-future ISO date (YYYY-MM-DD).', [rid])
        weight, error = _weight(fields.get('weight'), fields.get('weight_format') or decisions.get('weight_format'))
        if error:
            issue('weight_invalid', 'blocker', error, [rid])
        parent_ids = {'ticker': [token(fields.get('parent_ticker'))], 'isin': [token(fields.get('parent_isin'))], 'security_id': [token(fields.get('parent_id'))], 'cusip': [token(fields.get('parent_cusip'))], 'sedol': [token(fields.get('parent_sedol'))]}
        targets = matches(parent_ids, catalog)
        assignment = decisions.get('parent_assignment', {})
        parent_conflict = parent in catalog and contradicts(parent_ids, catalog[parent])
        if targets == [parent] and not parent_conflict:
            mapping = 'exact identifier'
        elif targets and parent not in targets:
            mapping = 'incorrect parent'
            issue('parent_mismatch', 'blocker', f"Source identifies {targets}, not selected {parent}. Select the correct parent or correct/exclude affected source rows.", [rid])
        elif not str(assignment.get('reason', '')).strip() or assignment.get('target') != parent:
            mapping = 'unresolved'
            issue('parent_ambiguous', 'blocker', f'Parent identifiers are missing, unmatched or ambiguous ({targets}); explicitly review assignment.', [rid])
        else:
            mapping = 'explicit reviewed assignment'
        ids = {key: [token(fields.get(key))] if token(fields.get(key)) else [] for key in ('ticker', 'isin', 'security_id', 'cusip', 'sedol')}
        override = decisions.get('identity_mappings', {}).get(rid)
        matched = matches(ids, catalog)
        if override:
            target = override.get('target')
            if target not in catalog or not str(override.get('reason', '')).strip():
                issue('identity_decision_invalid', 'blocker', 'Explicit identity mapping requires a currently held target and reason.', [rid])
            else:
                matched = [target]
        if not any(ids.values()):
            if override and matched:
                ids = deepcopy(catalog[matched[0]]['identifiers'])
            else:
                issue('identifier_missing', 'blocker', 'Supply a constituent security identifier or explicitly review its identity. Names alone are not identifiers.', [rid])
        if len(matched) == 1 and not override and contradicts(ids, catalog[matched[0]]):
            issue('identity_conflict', 'blocker', 'Ticker and stable identifier conflict with the accepted security. Correct the source or explicitly review identity.', [rid])
        if len(matched) > 1:
            issue('identity_ambiguous', 'blocker', f'Constituent identifiers refer to multiple accepted securities: {matched}. Review explicitly.', [rid])
        declared_source = fields.get('source') or str(decisions.get('source_label', '')).strip()
        if not declared_source:
            issue('source_unknown', 'warning', 'Provider/source information is missing; uploaded filename and SHA-256 remain available.', [rid])
        asset_class = fields.get('asset_class', '')
        if asset_class.lower() and not any(s in asset_class.lower() for s in ('equity', 'stock')):
            issue('non_equity', 'warning', 'Non-equity constituent is retained as metadata; its company/notional exposure is not inferred.', [rid])
        accepted.append({**deepcopy(record), 'parent': parent, 'reported_parent_identifiers': parent_ids, 'parent_mapping': mapping,
            'identifiers': ids, 'resolved_identifiers': deepcopy(catalog[matched[0]]['identifiers']) if override and len(matched) == 1 else deepcopy(ids),
            'matched_direct_security': matched[0] if len(matched) == 1 else None,
            'identity_status': 'explicit reviewed mapping' if override else 'exact identifier' if len(matched) == 1 else 'external security identifier' if not matched else 'unresolved',
            'name': fields.get('name', ''), 'weight': str(weight) if weight is not None else None,
            'as_of': date, 'source': declared_source or None, 'asset_class': asset_class, 'currency': token(fields.get('currency')) or None})
    # Shared identifiers need review. Do not discard or add duplicate weights silently.
    signatures, identifiers = defaultdict(list), defaultdict(list)
    for row in accepted:
        signatures[_hash([row['resolved_identifiers'], row['name'], row['weight'], row['as_of'], row['asset_class'], row['currency']])].append(row['record_id'])
        for kind, values in row['resolved_identifiers'].items():
            for value in values:
                identifiers[(kind, value)].append(row['record_id'])
    exact_groups = set()
    for rids in signatures.values():
        if len(rids) > 1:
            exact_groups.add(tuple(sorted(rids)))
            issue('duplicate_record', 'blocker', 'Identical constituent records require explicit exclusion/correction; nothing is deleted automatically.', rids)
    for rids in identifiers.values():
        if len(rids) > 1 and tuple(sorted(rids)) not in exact_groups:
            issue('identity_overlap', 'blocker', 'Constituent records share an identifier. Correct distinct instrument IDs or explicitly exclude a repeated row.', rids)
    # Deduplicate diagnostic groups, not economic records.
    issues = list({_hash(i): i for i in issues}.values())
    weights = [Decimal(r['weight']) for r in accepted if r['weight'] is not None]
    with localcontext() as context:
        context.prec = max(28, max((-w.as_tuple().exponent for w in weights), default=0) + len(str(len(weights))) + 5)
        total = sum(weights, Decimal(0))
        unreported = max(Decimal(0), 1-total)
        total_percent, missing_percent = total*100, unreported*100
    if total > 1:
        issue('weights_exceed_100', 'blocker', 'Total reported weight exceeds 100%; correct weights or duplicate records. No normalisation is applied.')
    if not accepted:
        issue('no_retained_records', 'blocker', 'At least one constituent record must remain.')
    age = None
    dates = {r['as_of'] for r in accepted if r['as_of']}
    if len(dates) == 1:
        age = (today - pd.Timestamp(next(iter(dates))).date()).days
        if age > max_age_days:
            issue('snapshot_stale', 'warning', f'Constituent snapshot is {age} calendar days old; configured freshness rule is {max_age_days} days.')
    declared = decisions.get('completeness', 'unspecified')
    complete = total == 1 and declared == 'complete' and not any(i['severity'] == 'blocker' for i in issues)
    weight_checks_resolved = not any(i['severity'] == 'blocker' for i in issues)
    if total < 1 and weight_checks_resolved:
        issue('partial_coverage', 'warning', f'Reported weights cover {total_percent}%; unreported weight is {missing_percent}%. Missing holdings are unknown, not zero.')
    elif not weight_checks_resolved:
        issue('coverage_unresolved', 'warning', 'Unresolved or invalid records prevent reliable coverage calculations; the valid-weight subtotal is provisional.')
    if declared == 'complete' and total != 1:
        issue('completeness_conflict', 'warning', 'Declared complete file does not report exactly 100% weight; coverage remains partial, without rescaling.')
    if total == 1 and not complete:
        issue('completeness_unverified', 'warning', '100% reported weight does not establish completeness without an explicit complete-dataset declaration and resolved identity checks.')
    issues.sort(key=lambda i: (i['severity'] != 'blocker', i['code'], i['record_ids']))
    untrusted_weights = any(i['severity'] == 'blocker' for i in issues)
    return {'parent': parent, 'parent_identity': deepcopy(catalog.get(parent)), 'snapshots': snapshots, 'snapshot_id': selected,
        'records': accepted, 'issues': issues, 'ready': bool(accepted) and not any(i['severity'] == 'blocker' for i in issues),
        'coverage': {'constituent_count': len(accepted), 'reported_weight': str(total), 'unreported_weight': None if untrusted_weights or (total == 1 and not complete) else str(unreported),
                     'known_weight_coverage': str(total) if not untrusted_weights and (total < 1 or complete) else None,
                     'completeness': 'invalid' if untrusted_weights else 'reported_complete' if complete else 'partial' if total < 1 or declared == 'partial' else 'unverified',
                     'as_of': next(iter(dates)) if len(dates) == 1 else None, 'age_days': age, 'max_age_days': max_age_days,
                     'source_labels': sorted({r['source'] for r in accepted if r['source']}),
                     'unresolved_identity_records': sorted({r for i in issues if i['code'] in {'identity_conflict', 'identity_ambiguous', 'identity_overlap', 'identifier_missing', 'parent_mismatch', 'parent_ambiguous'} for r in i['record_ids']})},
        'decision_basis': _hash([draft['fingerprint'], catalog, decisions, str(today), max_age_days]), 'decisions': decisions}


def accept_constituents(store, draft, catalog, *, decisions=None, as_of=None, max_age_days=90):
    """Revalidate at acceptance; invalid replacements leave prior store untouched."""
    checked = validate_constituents(draft, catalog, decisions=decisions, as_of=as_of, max_age_days=max_age_days)
    if not checked['ready']:
        raise ValueError('Resolve constituent validation blockers before accepting data.')
    result = deepcopy(store)
    parent = checked['parent']
    previous = result.get(parent)
    history = deepcopy(previous.get('history', [])) if previous else []
    if previous:
        history.append({k: deepcopy(v) for k, v in previous.items() if k != 'history'})
    result[parent] = {**checked, 'sources': deepcopy(draft['files']), 'raw_records': deepcopy(draft['records']),
                      'accepted_on': str(as_of or pd.Timestamp.today().date()), 'history': history,
                      'scope': 'supplementary fund metadata only; portfolio look-through is not calculated'}
    return result


def saved_coverage(snapshot, catalog, *, as_of=None, max_age_days=90):
    """Recompute freshness and parent compatibility without requests or valuation."""
    result = deepcopy(snapshot['coverage'])
    parent = catalog.get(snapshot['parent'])
    result['parent_current'] = parent is not None
    if parent and snapshot['parent_identity']:
        old_ids = snapshot['parent_identity']['identifiers']
        new_ids = parent['identifiers']
        result['parent_current'] = all(not old_ids[k] or set(old_ids[k]) == set(new_ids[k]) for k in old_ids)
    date = result['as_of']
    result['age_days'] = (pd.Timestamp(as_of or pd.Timestamp.today()).date() - pd.Timestamp(date).date()).days if date else None
    result['stale'] = result['age_days'] is None or result['age_days'] < 0 or result['age_days'] > max_age_days
    result['max_age_days'] = max_age_days
    return result
