# app/services/oauth_return.py
#
# Pulled out of main.py as its own module so it can be unit-tested without
# importing main.py itself (main.py pulls in jwt/cryptography at module
# scope, which several sandboxes - including this one - can't import due
# to a missing native _cffi_backend build; the OAuth login/callback flow
# using this function is unaffected in a real deployment, only local test
# collection is).


def safe_return_to(raw):
    """Only ever a same-app hash-route path, never an absolute URL - this
    value gets echoed straight back into a redirect after OAuth, so an
    unvalidated value would be an open-redirect vector (a login link
    crafted with return_to=https://evil.example could send a signed-in
    session there). Must start with '/' and never '//' (a browser treats
    a leading '//' as protocol-relative, i.e. still off-site)."""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    return raw
