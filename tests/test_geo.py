"""India location gate tests. No network, no DB.

Phase 1.1 of docs/PLAN.md consolidated three drifting city regexes into
common/geo.py, driven by config/settings.yaml. These tests pin down the two
things that consolidation could break: every keyword in the shipped config must
actually match, and the whole-word rule that keeps "Indiana" out must hold.

The false-positive cases are not hypothetical. Target's Workday state facet
carries 232 "Indiana" postings, and a substring match on "india" would have
admitted every one of them.

    python tests/test_geo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml

from common import geo
from common.geo import (FALLBACK_KEYWORDS, is_india_relevant, is_remote,
                        names_india)

SETTINGS = yaml.safe_load(
    (ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")) or {}
CONFIGURED = [str(w).strip().lower() for w in SETTINGS.get("location_keywords") or []]


def test_the_config_actually_supplies_the_vocabulary():
    """If this breaks, geo.py silently fell back and the config is dead again."""
    assert CONFIGURED, "config/settings.yaml has no location_keywords"
    assert len(CONFIGURED) > len(FALLBACK_KEYWORDS), \
        "the configured list should be wider than the emergency fallback"
    # The fallback is a safety net, not the live list; prove the live list is
    # what the matcher compiled by checking a keyword only the config has.
    assert "lucknow" in CONFIGURED
    assert names_india("Lucknow"), "the compiled pattern is not using the config"


def test_every_configured_keyword_matches_itself():
    for word in CONFIGURED:
        assert names_india(word), f"configured keyword {word!r} does not match"
        assert names_india(word.title()), f"{word!r} fails title-case"
        assert names_india(word.upper()), f"{word!r} fails upper-case"


def test_every_configured_keyword_matches_in_a_realistic_string():
    for word in CONFIGURED:
        for template in ("{}, India", "Office - {}", "{} (Hybrid)", "Remote - {}"):
            text = template.format(word.title())
            assert names_india(text), f"{text!r} did not match"


def test_the_cities_that_were_actually_missing_now_match():
    """The measured gap: 17 postings, all from India-HQ boards.

    Delhi was the real bug — the old list carried the NCR satellites Gurgaon,
    Gurugram and Noida but not the city they orbit.
    """
    for loc in ("Delhi", "New Delhi", "Lucknow", "Kolkata"):
        assert is_india_relevant(loc), loc


def test_the_original_eleven_still_match():
    """Consolidation must not lose what the old regex already caught."""
    for loc in ("Bengaluru, Karnataka, India", "Bangalore - EC, India, onsite",
                "Hyderabad", "Gurgaon", "Gurugram", "Pune", "Noida",
                "Chennai", "Mumbai", "India", "Karnataka"):
        assert is_india_relevant(loc), loc


def test_indiana_is_not_india():
    """232 postings on Target's state facet alone."""
    for loc in ("Indiana", "Indianapolis, Indiana", "Indianapolis, IN",
                "Bloomington, Indiana, United States"):
        assert not names_india(loc), loc
        assert not is_india_relevant(loc), loc


def test_the_bare_country_code_is_not_a_keyword():
    """"IN" case-insensitively matches the English word "in"."""
    assert "in" not in CONFIGURED, \
        'the bare country code "in" would match the word "in" everywhere'
    assert not is_india_relevant("Berlin, Germany")
    assert not is_india_relevant("Built in San Francisco")


def test_remote_alone_is_not_india():
    for loc in ("Remote", "Remote - United States", "Remote - USA",
                "Remote, United States", "United States (Remote)", "Hybrid"):
        assert not is_india_relevant(loc), loc


def test_remote_counts_when_the_description_names_india():
    assert is_india_relevant("Remote", "This role is open to candidates in India.")
    assert is_india_relevant("Remote - APAC", "Based out of our Bengaluru office.")
    assert not is_india_relevant("Remote", "This role is open to candidates in Berlin.")


def test_a_description_mention_does_not_rescue_a_non_remote_posting():
    """A San Francisco role that merely mentions an India office stays out."""
    assert not is_india_relevant(
        "San Francisco, CA", "We also have an office in Bengaluru.")


def test_an_empty_location_is_kept_for_layer_2_to_judge():
    assert is_india_relevant("")
    assert is_india_relevant(None)
    assert is_india_relevant("   ")


def test_names_india_does_not_apply_the_empty_string_policy():
    """The raw predicate is used for matching ATS facet labels, where an empty
    label is not evidence of anything."""
    assert not names_india("")
    assert not names_india(None)


def test_is_remote_recognises_the_usual_phrasings():
    for loc in ("Remote", "Anywhere", "Worldwide", "Global", "remote - us"):
        assert is_remote(loc), loc
    assert not is_remote("Bengaluru")
    assert not is_remote(None)


def test_the_real_dropped_locations_stay_dropped():
    """Sampled from the 12,030 postings the gate dropped on 2026-10-15."""
    for loc in ("San Francisco, Remote", "United States", "San Francisco",
                "San Francisco, CA | New York City, NY", "Tokyo, Japan",
                "Singapore", "SG - Singapore", "London, UK",
                "London, United Kingdom", "Paris, France", "Dublin, Ireland",
                "Santa Clara, California", "New York, New York, USA"):
        assert not is_india_relevant(loc), loc


def test_a_missing_config_falls_back_rather_than_matching_nothing():
    """Matching nothing would close every open job on the next run."""
    original = geo.SETTINGS
    try:
        geo.SETTINGS = ROOT / "config" / "does-not-exist.yaml"
        geo._reset_cache_for_tests()
        assert names_india("Bengaluru"), "fallback vocabulary is not in use"
        assert not names_india("Indiana")
    finally:
        geo.SETTINGS = original
        geo._reset_cache_for_tests()
    assert names_india("Lucknow"), "cache was not restored to the real config"


def test_the_operators_city_bengaluru_and_bangalore_are_one_place():
    from common.geo import in_preferred_location as ok
    assert ok("Bengaluru, Karnataka, India") and ok("Bangalore") and ok("BLR")
    assert ok("KA, India"), "how Indeed writes Bengaluru"
    assert ok("Bangalore, India, New Delhi, India, Mumbai, India"), "multi-city that includes it"
    assert not ok("Pune, Maharashtra") and not ok("Hyderabad") and not ok("Gurugram")
    assert not ok("Kalyan, Maharashtra"), "whole words only"
    assert ok("Remote, India"), "remote in India can be done from Bengaluru"
    assert not ok("Remote"), "remote with no India is Remote-US"
    assert ok("", "Our office in Bangalore") and not ok("", "Our office in Pune")


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  pass  {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  FAIL  {name}: {exc or 'assertion failed'}")
    print(f"\n{failed} failed" if failed else "\nall tests passed")
    sys.exit(1 if failed else 0)
