"""The README must not restate facts that go stale in silence.

Same seam as `test_skills_structure.py` and `test_docs_links.py`: this reads
shipped content and never imports the package's internals.

Two kinds of drift are pinned, both of which had already happened once -- the
compatibility summary claimed "Version 1.1.1 ... all six subcommands, five
skills" while the release was 1.4.0 with seven of each:

- a release version restated in the README, which no release step updates
  (the version lives in `pyproject.toml`, `validated_memory/__init__.py` and
  the plugin manifest, pinned together by `test_plugin_manifest.py`);
- a count of skills that no longer matches `skills/`.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
SKILLS_DIR = REPO_ROOT / "skills"

# "Version 1.4" or "version 1.4.0". A dot is required, so the moving major
# tag the README recommends for CI (`@v1`) is not a match.
RELEASE_VERSION_PATTERN = re.compile(r"\bv(?:ersion)?\s*\d+\.\d+", re.IGNORECASE)
SKILL_COUNT_PATTERN = re.compile(
    r"^(\w+) skills make the method invocable", re.MULTILINE
)
NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _readme():
    return README.read_text(encoding="utf-8")


def test_the_readme_restates_no_release_version():
    matches = RELEASE_VERSION_PATTERN.findall(_readme())
    assert not matches, (
        f"README states a release version ({matches}); nothing updates it at "
        "release time, so it drifts. Point at the manifest instead."
    )


def test_the_readme_skill_count_matches_the_skills_directory():
    text = _readme()
    match = SKILL_COUNT_PATTERN.search(text)
    assert match, "README no longer introduces the skills with a count"

    stated = NUMBER_WORDS.get(match.group(1).lower())
    assert stated is not None, f"unrecognized count word: {match.group(1)!r}"

    on_disk = len([path for path in SKILLS_DIR.iterdir() if path.is_dir()])
    assert stated == on_disk, (
        f"README says {match.group(1)} skills; skills/ has {on_disk}"
    )


HOOKS_MANIFEST = REPO_ROOT / "hooks" / "hooks.json"
# Every prose file that tells a reader how many hooks the plugin installs.
# `docs/installing.md` is on this list because it is the one that words the
# count differently, and was missed the first time.
HOOK_COUNT_FILES = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "installing.md",
    REPO_ROOT / "docs" / "adoption.md",
    REPO_ROOT / "docs" / "reference" / "hooks.md",
)
# Numeric startup claims may call the registrations hooks, entries or commands.
# Plain plural hook claims such as "other two hooks" also state a count even
# without naming SessionStart. The bounded middle group permits adjectives but
# does not let a match run across a sentence.
HOOK_COUNT_PATTERN = re.compile(
    r"\b(?:other\s+)?(one|two|three|four|five|six|seven|eight|nine|ten)\b"
    r"(?:\s+\S+){0,2}?\s+"
    r"(?:(?:`SessionStart`|SessionStart|startup)\s+"
    r"(?:hooks|entries|commands)|hooks)\b",
    re.IGNORECASE,
)
# "both hooks", "both startup hooks", "Both are fail-open" -- a count of two
# written as a word that no number pattern catches.
BOTH_HOOKS_PATTERN = re.compile(r"\bboth\b(?:\s+\S+){0,3}?\s+hooks?\b", re.IGNORECASE)


def _registered_hook_count():
    manifest = json.loads(HOOKS_MANIFEST.read_text(encoding="utf-8"))
    return sum(
        1
        for entry in manifest["hooks"]["SessionStart"]
        for hook in entry["hooks"]
        if hook.get("type") == "command"
    )


def _assert_hook_count_claims_match(text, count, label):
    matches = list(HOOK_COUNT_PATTERN.finditer(text))
    assert matches, (
        f"{label} no longer states the hook count; "
        "if that is deliberate, drop it from HOOK_COUNT_FILES"
    )
    for match in matches:
        word = match.group(1)
        stated = NUMBER_WORDS[word.lower()]
        is_relative = re.match(r"other\s", match.group(0), re.IGNORECASE)
        expected_count = count - 1 if is_relative else count
        assert stated == expected_count, (
            f"{label} says {match.group(0)!r}; hooks.json registers {count}"
        )


def test_hook_count_pattern_covers_startup_claims_without_matching_a_singular_hook():
    positive = {
        "Three `SessionStart` hooks run in order.": "Three",
        "Four SessionStart entries are registered.": "Four",
        "There are two startup commands.": "two",
        "The other two hooks remain unchanged.": "two",
        "All three ordered startup hooks are fail-open.": "three",
    }
    negative = (
        "One UserPromptSubmit hook performs discovery.",
        "Four transfer entries are retained.",
        "SessionStart runs during startup.",
    )

    for text, count_word in positive.items():
        assert HOOK_COUNT_PATTERN.findall(text) == [count_word]
    for text in negative:
        assert HOOK_COUNT_PATTERN.findall(text) == []

    with pytest.raises(AssertionError, match="hooks.json registers 3"):
        _assert_hook_count_claims_match(
            "Four SessionStart entries are registered.", 3, "mutant.md"
        )


def test_every_prose_statement_of_the_hook_count_matches_the_manifest():
    # The count lives in `hooks/hooks.json` and is restated in four prose
    # files. Nothing updates those at registration time, so they drift -- and
    # a reader who is told "two" while three run has been told something
    # false about what installing the plugin does to their machine.
    count = _registered_hook_count()
    for path in HOOK_COUNT_FILES:
        text = path.read_text(encoding="utf-8")
        _assert_hook_count_claims_match(text, count, path.relative_to(REPO_ROOT))
        if count != 2:
            leftover = BOTH_HOOKS_PATTERN.search(text)
            assert leftover is None, (
                f"{path.relative_to(REPO_ROOT)} still says "
                f"{leftover.group(0)!r}, which means two; hooks.json "
                f"registers {count}"
            )
