from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO / path).read_text(encoding="utf-8")


def test_userscript_observer_is_not_fc26_locked():
    source = _read("eafc-live-userscript/src/page-hook.js")
    assert "FC_GAME_RE" in source
    assert r"fc\d+" in source
    assert "FC26_RE" not in source
    assert "/ut/game/fc26" not in source


def test_extension_observers_are_not_fc26_locked():
    bridge = _read("eafc-live-extension/src/page-bridge.js")
    session = _read("eafc-live-extension/src/ea-session.js")
    assert "FC_GAME_RE" in bridge
    assert "FC_GAME_PATH_RE" in session
    assert r"fc\d+" in bridge
    assert r"fc\d+" in session
    assert "FC26_RE" not in bridge
    assert "FC26_PATH_RE" not in session


def test_customer_companion_copy_is_season_neutral():
    metadata = _read("eafc-live-userscript/src/metadata.js")
    launcher = _read("frontend/src/pages/Companion.jsx")
    assert "@version      1.10.0" in metadata
    assert "EA SPORTS FC Web App Transfer Market" in metadata
    assert "EA SPORTS FC 26 Web App" not in metadata
    assert "EA SPORTS FC Web App Transfer Market" in launcher
    assert "EA SPORTS FC 26 Web App" not in launcher


def test_frontend_build_prepares_the_served_userscript():
    package = _read("frontend/package.json")
    prepare = _read("frontend/scripts/prepare-companion.mjs")
    assert "node scripts/prepare-companion.mjs && vite build" in package
    assert "1.10.0" in prepare
    assert "FC_GAME_RE" in prepare
    assert r"fc\\d+" in prepare
    assert "expected ${label} was not found" in prepare
