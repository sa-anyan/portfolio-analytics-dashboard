"""Five accepted questions per signed IP for one finite demo; no automatic reset.

Only the verified ingress signer may issue assertions. IPs are never persisted:
the quota key is an HMAC under a separate, private, demo-specific salt.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import hmac
import ipaddress
import os
import re
import sqlite3
import time
from uuid import uuid4

from portfolio_analytics.security.ai_access import AIUnavailable, _actor, _connect, policy

_question = ContextVar('demo_question', default=None)
EXHAUSTED = 'Thanks for trying the demo! This network has used its five AI questions. You can still explore all deterministic analytics and scenarios.'


def configuration(*, allow_expired=False):
    try:
        demo = os.environ['AI_DEMO_ID']
        end = int(os.environ['AI_DEMO_END_EPOCH'])
        secret = bytes.fromhex(os.environ['AI_PROXY_SIGNING_KEY'])
        salt = bytes.fromhex(os.environ['AI_IP_HASH_KEY'])
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', demo) or len(secret) != 32 or len(salt) != 32
                or secret == salt or os.getenv('AI_PROXY_VERIFIED') != 'signed_nginx_single_host'
                or end <= 0 or (not allow_expired and end <= time.time())):
            raise ValueError()
        return demo, end, secret, salt
    except (KeyError, ValueError):
        raise AIUnavailable('Public AI demo is unavailable: verified proxy, private quota keys or demo dates are not configured.') from None


def canonical_ip(value):
    if not isinstance(value, str) or len(value) > 45 or '%' in value:
        raise ValueError('Invalid IP')
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address)


def sign_ip(value, timestamp=None, *, allow_expired=False):
    """Used only by the private ingress signer, never exposed to a browser."""
    demo, _, secret, _ = configuration(allow_expired=allow_expired)
    value = canonical_ip(value)
    timestamp = str(int(time.time()) if timestamp is None else int(timestamp))
    signature = hmac.new(secret, f'{demo}\0{value}\0{timestamp}'.encode(), sha256).hexdigest()
    return {'X-Demo-IP': value, 'X-Demo-Time': timestamp, 'X-Demo-Signature': signature}


def verified_actor(headers):
    demo, _, secret, salt = configuration()
    try:
        def single(name):
            values = headers.get_all(name) if hasattr(headers, 'get_all') else [headers[name]]
            if len(values) != 1 or not isinstance(values[0], str):
                raise ValueError()
            return values[0]
        ip = canonical_ip(single('X-Demo-IP'))
        timestamp = single('X-Demo-Time')
        age = time.time() - int(timestamp)
        if not 0 <= age <= 12 * 3600:
            raise ValueError()
        expected = hmac.new(secret, f'{demo}\0{ip}\0{timestamp}'.encode(), sha256).hexdigest()
        if not hmac.compare_digest(expected, single('X-Demo-Signature')):
            raise ValueError()
        return hmac.new(salt, f'{demo}\0{ip}'.encode(), sha256).hexdigest()
    except (KeyError, ValueError, TypeError):
        raise AIUnavailable('AI is unavailable because your network identity could not be verified. Refresh or ask the demo host for help.') from None


def _binding():
    demo, end, secret, salt = configuration()
    return (demo, str(end), sha256(secret).hexdigest(), sha256(salt).hexdigest())


def provision_demo(path):
    """Explicit offline addition to an existing private budget DB; never auto-created."""
    p = policy()
    if p.mode != 'public_demo' or str(p.path) != str(path):
        raise ValueError('Provision the configured public demo budget.')
    demo, end, secret, salt = _binding()
    if int(end) > time.time() + 30 * 86400:
        raise ValueError('The demo must end within 30 days; extending it requires review.')
    with _connect(p) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE demo_config (demo TEXT PRIMARY KEY, end TEXT NOT NULL, signing_digest TEXT NOT NULL, salt_digest TEXT NOT NULL)')
        db.execute('INSERT INTO demo_config VALUES (?,?,?,?)', (demo,end,secret,salt))
        db.execute('CREATE TABLE demo_questions (id TEXT PRIMARY KEY, actor TEXT NOT NULL, created REAL NOT NULL)')
        db.execute('CREATE INDEX demo_actor ON demo_questions(actor)')


def _check(db):
    if db.execute('SELECT demo,end,signing_digest,salt_digest FROM demo_config').fetchall() != [_binding()]:
        raise AIUnavailable('Demo quota configuration changed; operator review is required. Allowances cannot be reset.')


def remaining(headers):
    p = policy()
    if p.mode != 'public_demo':
        raise AIUnavailable('Public demo is disabled.')
    actor = verified_actor(headers)
    try:
        with _connect(p) as db:
            _check(db)
            used = db.execute('SELECT COUNT(*) FROM demo_questions WHERE actor=?', (actor,)).fetchone()[0]
        return max(0, 5 - used)
    except (sqlite3.Error, OSError):
        raise AIUnavailable('Demo quota storage is unavailable; AI requests are paused.') from None


@contextmanager
def authorised_demo_question(headers, consent, question):
    p = policy()
    if p.mode != 'public_demo' or not consent:
        raise AIUnavailable('Consent to external AI submission is required.')
    if not isinstance(question, str) or not question.strip() or len(question) > 1000:
        raise AIUnavailable('Enter a question of at most 1,000 characters.')
    actor = verified_actor(headers)
    rid = str(uuid4())
    try:
        with _connect(p) as db:
            db.execute('BEGIN IMMEDIATE')
            _check(db)
            used = db.execute('SELECT COUNT(*) FROM demo_questions WHERE actor=?', (actor,)).fetchone()[0]
            if used >= 5:
                raise AIUnavailable(EXHAUSTED)
            # Debit once before any provider attempt. Failures are never refunded.
            db.execute('INSERT INTO demo_questions VALUES (?,?,?)', (rid,actor,time.time()))
        actor_token = _actor.set(actor)
        question_token = _question.set({'id':rid, 'actor':actor, 'attempts':0})
        try:
            yield
        finally:
            _question.reset(question_token)
            _actor.reset(actor_token)
    except (sqlite3.Error, OSError):
        raise AIUnavailable('Demo quota storage is unavailable; no further AI requests are allowed.') from None


def provider_attempt(db, p, actor, tokens):
    """Called within the existing atomic provider reservation transaction."""
    _check(db)
    question = _question.get()
    if not question or question['actor'] != actor or question['attempts'] >= 2:
        raise AIUnavailable('A verified submitted demo question is required; at most two provider attempts are allowed.')
    if not db.execute('SELECT 1 FROM demo_questions WHERE id=? AND actor=?', (question['id'],actor)).fetchone():
        raise AIUnavailable('The submitted demo question cannot be verified.')
    count, reserved = db.execute('SELECT COUNT(*),COALESCE(SUM(tokens),0) FROM reservations').fetchone()
    if count >= p.demo_requests or reserved + tokens > p.demo_tokens:
        raise AIUnavailable('The shared demonstration AI allowance is exhausted. Deterministic analytics remain available.')
    question['attempts'] += 1
