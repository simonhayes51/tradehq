"""safe_return_to() guards the OAuth post-login redirect against open
redirects - a login link crafted with return_to=https://evil.example (or
a protocol-relative //evil.example) must never be echoed back into the
RedirectResponse after Discord auth succeeds."""
from app.services.oauth_return import safe_return_to


def test_safe_relative_path_is_kept():
    assert safe_return_to("/v2/players/34567") == "/v2/players/34567"


def test_none_defaults_to_root():
    assert safe_return_to(None) == "/"


def test_empty_string_defaults_to_root():
    assert safe_return_to("") == "/"


def test_absolute_url_is_rejected():
    assert safe_return_to("https://evil.example/steal") == "/"


def test_protocol_relative_url_is_rejected():
    assert safe_return_to("//evil.example/steal") == "/"


def test_path_without_leading_slash_is_rejected():
    assert safe_return_to("v2/players/1") == "/"
