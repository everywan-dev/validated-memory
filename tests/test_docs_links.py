"""Link and asset checks over the repository's Markdown surface.

Same seam as `test_skills_structure.py`: these tests read shipped content —
README, docs, skills — and never import the package's internals. They exist
because the documentation split moved the reference manual out of the README:
every relative link and `#fragment` that used to resolve by scrolling now
crosses files, and a broken one is exactly the defect nobody notices until a
reader clicks it.

Pinned here:

- every relative Markdown link points at a path that exists;
- every `#fragment` — same-file or cross-file — names a real heading in the
  target document, using GitHub's slug rules;
- every `<picture>` block carries dark and light sources plus an `<img>`
  fallback with `alt` text, and every image path it names exists;
- the light/dark SVG pairs under `docs/assets/` stay textually identical, so
  the two themes can never drift apart in what they say;
- no SVG asset references an external resource.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = REPO_ROOT / "docs" / "assets"

FENCE_PATTERN = re.compile(r"^(```|~~~).*?^\1\s*$", re.DOTALL | re.MULTILINE)
CODE_SPAN_PATTERN = re.compile(r"`[^`\n]*`")
LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
# A badge link nests one link inside another: [![alt](img)](target). The
# pattern above only sees the inner (img), so the outer target is matched
# separately by its distinctive ')](' seam.
WRAPPED_LINK_PATTERN = re.compile(r"\)\]\(([^)\s]+)\)")
HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
PICTURE_PATTERN = re.compile(r"<picture>(.*?)</picture>", re.DOTALL)
IMG_SRC_PATTERN = re.compile(r'(?:srcset|src)="([^"]+)"')
SVG_TEXT_PATTERN = re.compile(r"<text[^>]*>(.*?)</text>", re.DOTALL)


def _prose_files():
    files = [REPO_ROOT / "README.md", REPO_ROOT / "CONTRIBUTING.md"]
    files += sorted((REPO_ROOT / "docs").rglob("*.md"))
    files += sorted((REPO_ROOT / "skills").glob("*/SKILL.md"))
    return files


def _without_code(text):
    return CODE_SPAN_PATTERN.sub("", FENCE_PATTERN.sub("", text))


def _gfm_slug(heading):
    # GitHub's slugger: formatting stripped, lowercased, punctuation dropped
    # except hyphens and underscores, spaces become hyphens.
    heading = heading.replace("`", "").strip().lower()
    out = []
    for char in heading:
        if char.isalnum() or char in "-_":
            out.append(char)
        elif char == " ":
            out.append("-")
    return "".join(out)


def _slugs(markdown_path):
    # Only fences are stripped here: a heading like ### `init` keeps its code
    # span, whose backticks the slugger itself removes.
    text = FENCE_PATTERN.sub("", markdown_path.read_text(encoding="utf-8"))
    slugs = set()
    seen = {}
    for match in HEADING_PATTERN.finditer(text):
        slug = _gfm_slug(match.group(1))
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        slugs.add(slug if count == 0 else f"{slug}-{count}")
    return slugs


def _relative_links(path):
    text = _without_code(path.read_text(encoding="utf-8"))
    for pattern in (LINK_PATTERN, WRAPPED_LINK_PATTERN):
        for match in pattern.finditer(text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            yield target


def test_every_relative_link_resolves():
    for path in _prose_files():
        for target in _relative_links(path):
            location, _, fragment = target.partition("#")
            if location:
                resolved = (path.parent / location).resolve()
                assert resolved.exists(), (
                    f"{path.relative_to(REPO_ROOT)} links to '{location}', "
                    "which does not exist"
                )
            else:
                resolved = path
            if fragment:
                assert resolved.suffix == ".md", (
                    f"{path.relative_to(REPO_ROOT)} uses fragment "
                    f"'#{fragment}' on non-Markdown '{location}'"
                )
                assert fragment in _slugs(resolved), (
                    f"{path.relative_to(REPO_ROOT)} links to "
                    f"'{target}', but '{resolved.relative_to(REPO_ROOT)}' "
                    f"has no heading with slug '{fragment}'"
                )


def test_picture_blocks_are_complete_and_their_images_exist():
    found_any = False
    for path in _prose_files():
        text = path.read_text(encoding="utf-8")
        for block in PICTURE_PATTERN.finditer(text):
            found_any = True
            content = block.group(1)
            for scheme in ("dark", "light"):
                assert f'media="(prefers-color-scheme: {scheme})"' in content, (
                    f"{path.relative_to(REPO_ROOT)}: <picture> without a "
                    f"{scheme} source"
                )
            img = re.search(r"<img\b[^>]*>", content)
            assert img, f"{path.relative_to(REPO_ROOT)}: <picture> without an <img> fallback"
            assert 'alt="' in img.group(0), (
                f"{path.relative_to(REPO_ROOT)}: <picture>'s <img> has no alt text"
            )
            for src in IMG_SRC_PATTERN.finditer(content):
                resolved = (path.parent / src.group(1)).resolve()
                assert resolved.exists(), (
                    f"{path.relative_to(REPO_ROOT)} names '{src.group(1)}', "
                    "which does not exist"
                )
    assert found_any, "expected at least one <picture> block in the prose"


def test_light_and_dark_svg_variants_say_the_same_thing():
    lights = sorted(ASSETS_DIR.glob("*-light.svg"))
    assert lights, "expected light/dark SVG pairs under docs/assets/"
    for light in lights:
        dark = light.with_name(light.name.replace("-light.svg", "-dark.svg"))
        assert dark.is_file(), f"{light.name} has no dark variant"
        light_texts = SVG_TEXT_PATTERN.findall(light.read_text(encoding="utf-8"))
        dark_texts = SVG_TEXT_PATTERN.findall(dark.read_text(encoding="utf-8"))
        assert light_texts == dark_texts, (
            f"{light.name} and {dark.name} carry different text content"
        )


def test_svg_assets_reference_nothing_external():
    svgs = sorted(ASSETS_DIR.glob("*.svg"))
    assert svgs, "expected SVG assets under docs/assets/"
    for svg in svgs:
        text = svg.read_text(encoding="utf-8")
        stripped = text.replace('xmlns="http://www.w3.org/2000/svg"', "")
        assert "http" not in stripped, (
            f"{svg.name} references an external resource"
        )
