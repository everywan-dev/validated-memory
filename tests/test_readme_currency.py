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

import re
from pathlib import Path

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
