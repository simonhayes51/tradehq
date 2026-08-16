# tests/test_fix_futgg_name_mojibake.py
#
# Pure-logic tests for scripts/fix_futgg_name_mojibake.py's try_fix() -
# the round-trip detector/repair for UTF-8-decoded-as-Latin-1 mojibake
# in scraped futgg_players text columns (e.g. "Kylian MbappÃ©").
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from fix_futgg_name_mojibake import try_fix  # noqa: E402


def test_try_fix_repairs_the_reported_mbappe_case():
    assert try_fix("Kylian MbappÃ©") == "Kylian Mbappé"


def test_try_fix_repairs_other_common_diacritics():
    # Corrupted strings are derived from the clean ones (rather than
    # hand-typed) to guarantee they're byte-for-byte what this exact
    # corruption actually produces: encode as UTF-8 (what should have
    # been stored), then decode those bytes as Latin-1 (the bug).
    for clean in ("Ángel Di María", "Koundé", "Güler", "Núñez"):
        corrupted = clean.encode("utf-8").decode("latin-1")
        assert try_fix(corrupted) == clean


def test_try_fix_leaves_already_correct_text_alone():
    for clean in ("Kylian Mbappé", "Ángel Di María", "Erling Haaland", "Rodri"):
        assert try_fix(clean) is None


def test_try_fix_leaves_plain_ascii_alone():
    assert try_fix("Bukayo Saka") is None
    assert try_fix("") is None


def test_try_fix_does_not_touch_a_string_with_no_latin1_round_trip():
    # A genuine non-Latin-1-representable character (e.g. outside U+0000-
    # U+00FF) must fail the encode step and be left alone rather than
    # raising.
    assert try_fix("K-pop Demon Hunters 👹") is None
