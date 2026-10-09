"""Fail-closed OIDC access and durable single-host request/token reservations.

No dollar-spend guarantee. Multiple processes must share one local persistent DB.
Multi-host/NFS deployments are unsupported and must keep paid AI disabled.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
import time
from importlib.metadata import version, PackageNotFoundError
from uuid import uuid4


class AIUnavailable(RuntimeError):
    pass


_actor = ContextVar('paid_ai_actor', default=None)


@dataclass(frozen=True)
class Policy:
    path: Path
    issuer: str
    allowed_subjects: frozenset
    user_requests: int = 10
    user_tokens: int = 100_000
    global_requests: int = 100
    global_tokens: int = 1_000_000
    concurrency: int = 2
    max_input_bytes: int = 32_000
    max_output_tokens: int = 1000
    mode: str = 'controlled_oidc'
    demo_requests: int = 20
    demo_tokens: int = 500_000


def policy():
    if os.getenv('OPENAI_LOG', '').lower() not in {'', 'warning', 'error', 'critical'}:
        raise AIUnavailable('Verbose provider logging is prohibited for portfolio submissions.')
    mode = os.getenv('AI_ACCESS_POLICY', 'disabled')
    if mode not in {'controlled_oidc', 'public_demo'}:
        raise AIUnavailable('Paid Copilot is disabled by server policy.')
    if os.getenv('AI_DEPLOYMENT_ARCHITECTURE') != 'single_host':
        raise AIUnavailable('Paid Copilot requires verified single-host budget storage.')
    path = Path(os.getenv('AI_BUDGET_DB_PATH', ''))
    issuer = os.getenv('AI_OIDC_ISSUER', '').strip()
    subjects = frozenset(s.strip() for s in os.getenv('AI_ALLOWED_SUBJECTS', '').split(',') if s.strip())
    if not path.is_absolute() or (mode == 'controlled_oidc' and (not issuer.startswith('https://') or not subjects)):
        raise AIUnavailable('Paid Copilot configuration is incomplete.')
    def limit(name, default):
        try:
            value = int(os.getenv(name, default))
            if not 1 <= value <= default:
                raise ValueError()
            return value
        except ValueError:
            raise AIUnavailable('Paid Copilot limit configuration is invalid.') from None
    if mode == 'public_demo':
        from portfolio_analytics.security.demo_quota import configuration
        configuration()
    return Policy(path, issuer, subjects,
        user_requests=limit('AI_USER_DAILY_REQUESTS', 10), user_tokens=limit('AI_USER_DAILY_TOKENS', 100_000),
        global_requests=limit('AI_GLOBAL_MONTHLY_REQUESTS', 100), global_tokens=limit('AI_GLOBAL_MONTHLY_TOKENS', 1_000_000),
        concurrency=limit('AI_MAX_CONCURRENT_REQUESTS', 1 if mode == 'public_demo' else 2), mode=mode,
        demo_requests=limit('AI_DEMO_TOTAL_REQUESTS', 20), demo_tokens=limit('AI_DEMO_TOTAL_TOKENS', 500_000))


def verify_auth_stack():
    try:
        if tuple(int(v) for v in version('cryptography').split('.')[:2]) < (50,0) or tuple(int(v) for v in version('authlib').split('.')[:2]) < (1,8):
            raise AIUnavailable('Paid Copilot requires the patched controlled Linux authentication bundle.')
    except (PackageNotFoundError, ValueError):
        raise AIUnavailable('Paid Copilot requires the patched controlled Linux authentication bundle.') from None


def identity(user):
    """Only call with Streamlit's server-verified OIDC user, never form fields."""
    p = policy()
    if p.mode != 'controlled_oidc':
        raise AIUnavailable('This service requires a verified demo question.')
    verify_auth_stack()
    try:
        if not user.is_logged_in or user.get('iss') != p.issuer or user.get('sub') not in p.allowed_subjects:
            raise AIUnavailable('Sign in with an authorised account to use paid Copilot.')
        if float(user.get('exp', 0)) <= time.time():
            raise AIUnavailable('Sign in again; your AI identity has expired.')
        return sha256((p.issuer+'\0'+user['sub']).encode()).hexdigest()
    except (AttributeError, TypeError, ValueError):
        raise AIUnavailable('A verified AI identity is required.') from None


@contextmanager
def authorised_request(user, consent):
    if not consent:
        raise AIUnavailable('Consent to external AI submission is required.')
    actor = identity(user)
    token = _actor.set(actor)
    try:
        yield
    finally:
        _actor.reset(token)


def _connect(p):
    # Storage provisioning is operator-owned: never silently create an empty budget.
    if p.path.is_symlink() or not p.path.is_file() or p.path.parent.is_symlink():
        raise AIUnavailable('AI budget storage cannot be verified.')
    if p.path.stat().st_mode & 0o077 or p.path.parent.stat().st_mode & 0o077:
        raise AIUnavailable('AI budget storage permissions are unsafe.')
    db = sqlite3.connect(f'file:{p.path}?mode=rw', uri=True, timeout=2)
    return db


def initialise_budget(path):
    """Explicit offline operator provisioning; not invoked by application startup."""
    path = Path(path)
    if not path.is_absolute() or path.exists():
        raise ValueError('Use a new absolute database path in a private persistent directory.')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.stat().st_mode & 0o077:
        raise ValueError('Budget directory must be private (0700).')
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(fd)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE reservations (id TEXT PRIMARY KEY, actor TEXT NOT NULL, created REAL NOT NULL, day TEXT NOT NULL, month TEXT NOT NULL, tokens INTEGER NOT NULL, active INTEGER NOT NULL)')


@contextmanager
def reserve(input_bytes, output_tokens):
    p = policy()
    actor = _actor.get()
    if not actor:
        raise AIUnavailable('A verified AI request is required.')
    if input_bytes > p.max_input_bytes or not 1 <= output_tokens <= p.max_output_tokens:
        raise AIUnavailable('AI context or output limit exceeded; reduce the portfolio/question.')
    # UTF-8 bytes conservatively bound visible text tokenisation. Overhead reserved too.
    tokens = input_bytes + output_tokens + 512
    now = time.time(); date = datetime.fromtimestamp(now, timezone.utc)
    day = date.date().isoformat(); month = day[:7]; rid = str(uuid4())
    try:
        with _connect(p) as db:
            db.execute('BEGIN IMMEDIATE')
            # Crashes retain budget debit and concurrency until operator reconciliation.
            user_count, user_tokens = db.execute('SELECT COUNT(*), COALESCE(SUM(tokens),0) FROM reservations WHERE actor=? AND day=?', (actor,day)).fetchone()
            global_count, global_tokens = db.execute('SELECT COUNT(*), COALESCE(SUM(tokens),0) FROM reservations WHERE month=?', (month,)).fetchone()
            active = db.execute('SELECT COUNT(*) FROM reservations WHERE active=1').fetchone()[0]
            if p.mode == 'public_demo':
                from portfolio_analytics.security.demo_quota import provider_attempt
                provider_attempt(db, p, actor, tokens)
            if p.mode != 'public_demo' and (user_count >= p.user_requests or user_tokens+tokens > p.user_tokens):
                raise AIUnavailable('Your daily AI request/token allowance is exhausted.')
            if global_count >= p.global_requests or global_tokens+tokens > p.global_tokens:
                raise AIUnavailable('Shared monthly AI request/token allowance is exhausted.')
            if active >= p.concurrency:
                raise AIUnavailable('AI is busy; try later. No request was sent.')
            db.execute('INSERT INTO reservations VALUES (?,?,?,?,?,?,1)', (rid,actor,now,day,month,tokens))
        yield
    except AIUnavailable:
        raise
    except (sqlite3.Error, OSError):
        raise AIUnavailable('AI budget storage is unavailable; no further requests are allowed.') from None
    finally:
        # A failed release keeps the lease; never refund uncertain or failed paid attempts.
        try:
            with _connect(p) as db:
                db.execute('UPDATE reservations SET active=0 WHERE id=?', (rid,))
        except (sqlite3.Error, AIUnavailable, OSError):
            pass
