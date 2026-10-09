"""Session scoping without pretending to provide cross-session persistence."""
from hashlib import sha256


def synchronise_owner(state, user):
    owner = 'anonymous'
    if getattr(user, 'is_logged_in', False):
        owner = sha256((str(user.get('iss', ''))+'\0'+str(user.get('sub',''))).encode()).hexdigest()
    previous = state.get('_workspace_owner')
    if previous is not None and previous != owner:
        state.clear()
    state['_workspace_owner'] = owner
