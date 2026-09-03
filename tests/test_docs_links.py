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
- no SVG asset references an external resource;
- inside `validated_memory/` and `tests/`, every documentary reference in
  a docstring or a comment resolves: a path under `docs/` ending in `.md`
  is versioned and exists, a section or fragment mark it carries names a
  real heading, and `ADR NNNN` matches exactly one file under
  `docs/adr/`; a section is never cited by number alone, ambiguous
  between the two design documents this project has; and none of it
  points outside the repository -- into the gitignored session log, or
  by naming a step of a plan rather than the thing the plan produced.
"""

import ast
import io
import re
import subprocess
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = REPO_ROOT / "docs" / "assets"

FENCE_PATTERN = re.compile(r"^(```|~~~).*?^\1\s*$", re.DOTALL | re.MULTILINE)
CODE_SPAN_PATTERN = re.compile(r"`[^`\n]*`")
# The optional trailing group accepts a link title: [x](file.md "title").
LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# A badge link nests one link inside another: [![alt](img)](target). The
# pattern above only sees the inner (img), so the outer target is matched
# separately by its distinctive ')](' seam.
WRAPPED_LINK_PATTERN = re.compile(r"\)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
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


def test_inline_images_exist_and_carry_alt():
    for path in _prose_files():
        text = path.read_text(encoding="utf-8")
        for img in re.finditer(r"<img\b[^>]*>", text):
            tag = img.group(0)
            assert 'alt="' in tag, (
                f"{path.relative_to(REPO_ROOT)}: <img> without alt text"
            )
            for src in IMG_SRC_PATTERN.finditer(tag):
                target = src.group(1)
                if target.startswith(("http://", "https://")):
                    continue
                resolved = (path.parent / target).resolve()
                assert resolved.exists(), (
                    f"{path.relative_to(REPO_ROOT)} names '{target}', "
                    "which does not exist"
                )


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


# --- documentary references inside Python source ---------------------------
#
# Same shape as the Markdown checks above, over a different surface: a
# docstring or a comment under `validated_memory/` or `tests/` that cites a
# design document, an ADR, or a section of one. A comment or a docstring is
# this surface's "prose" -- the analogue of `_without_code` above -- so an
# ordinary string literal (YAML fixture content, an error message) is never
# scanned: it is not documentation, and treating it as such would flag
# `test_validate.py`'s deliberately fake Markdown-path fixture values as
# broken references they were never meant to be.
#
# This exists because three references once named a step of an archived,
# gitignored plan that a fresh clone had nothing to resolve against, and
# twenty-three more named a section number with no document at all,
# ambiguous between the two design documents that both have a "§4".

PY_DOC_PATH_PATTERN = re.compile(r"docs/[\w./-]+\.md")
# The gap this project's own wrapped citations use between the path and its
# section mark: a closing paren, backtick, quote, comma, colon, hyphen, a
# comment's own `#` marker, or a line break into the next comment line --
# never more than this, so an unrelated `§N` appearing later in the same
# docstring is never mistaken for the citation's own section.
SECTION_GAP = r"[\s)`\"',:#-]{0,20}"
PY_SECTION_PATTERN = re.compile(SECTION_GAP + r"§(\d+)")
PY_FRAGMENT_PATTERN = re.compile(r"#([\w-]+)")
ADR_PATTERN = re.compile(r"\bADR (\d{4})\b")
# The exact shape of the other original defect: a section cited by number
# alone, naming no document. `design §N` never appears in a resolved
# citation -- that reads `docs/<file>.md §N` instead -- so any surviving
# match is the ambiguity this project's two design documents created,
# back again.
BARE_DESIGN_SECTION_PATTERN = re.compile(r"\bdesign §\d", re.IGNORECASE)
# A pointer into a plan that never shipped as repository content: the
# shape the three original broken references took, naming a step of an
# executed plan instead of the thing the plan produced.
FORBIDDEN_POINTER_PATTERN = re.compile(r"\bTask \d+\b|the step's brief", re.IGNORECASE)
SESSIONS_PATTERN = re.compile(r"\bsessions/")
SECTION_HEADING_TEMPLATE = r"^## {}\.\s"


def _python_files():
    files = sorted((REPO_ROOT / "validated_memory").rglob("*.py"))
    files += sorted((REPO_ROOT / "tests").glob("*.py"))
    return files


def _docstrings(tree):
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            doc = ast.get_docstring(node)
            if doc:
                yield doc


def _comment_blocks(source):
    # Consecutive same-column comment lines are one logical paragraph, the
    # way this project wraps a citation across a `#`-prefixed line break;
    # anything else -- code, a blank line, a dedented comment -- starts a
    # new one, so a reference never bleeds into an unrelated block.
    blocks = []
    current = []
    prev_line = prev_col = None
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            line, col = tok.start
            if current and line == prev_line + 1 and col == prev_col:
                current.append(tok.string)
            else:
                if current:
                    blocks.append("\n".join(current))
                current = [tok.string]
            prev_line, prev_col = line, col
        elif tok.type in (
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENCODING,
        ):
            continue
        else:
            if current:
                blocks.append("\n".join(current))
                current = []
            prev_line = prev_col = None
    if current:
        blocks.append("\n".join(current))
    return blocks


def _documentary_chunks(path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    chunks = list(_docstrings(tree))
    chunks.extend(_comment_blocks(source))
    return chunks


def _tracked_files():
    # A versioned path is one that survives to a fresh clone: tracked by
    # git, not merely present on the machine that wrote the reference --
    # exactly the distinction the three original broken references
    # crossed. `None` means git could not answer (no `.git`, no git on
    # PATH); callers fall back to a plain existence check.
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return {
        (REPO_ROOT / line).resolve()
        for line in result.stdout.splitlines()
        if line
    }


def _section_exists(markdown_path, number):
    text = markdown_path.read_text(encoding="utf-8")
    pattern = SECTION_HEADING_TEMPLATE.format(re.escape(number))
    return re.search(pattern, text, re.MULTILINE) is not None


def test_every_python_doc_path_reference_is_versioned_and_resolves():
    tracked = _tracked_files()
    for path in _python_files():
        for chunk in _documentary_chunks(path):
            for match in PY_DOC_PATH_PATTERN.finditer(chunk):
                target = match.group(0)
                resolved = (REPO_ROOT / target).resolve()
                label = f"{path.relative_to(REPO_ROOT)} cites '{target}'"
                if tracked is not None:
                    assert resolved in tracked, (
                        f"{label}, which is not a versioned path"
                    )
                else:
                    assert resolved.is_file(), f"{label}, which does not exist"
                rest = chunk[match.end() : match.end() + 60]
                fragment = PY_FRAGMENT_PATTERN.match(rest)
                section = None if fragment else PY_SECTION_PATTERN.match(rest)
                if fragment:
                    slug = fragment.group(1)
                    assert slug in _slugs(resolved), (
                        f"{label} with fragment '#{slug}', but '{target}' "
                        f"has no heading with slug '{slug}'"
                    )
                elif section:
                    number = section.group(1)
                    assert _section_exists(resolved, number), (
                        f"{label} §{number}, but '{target}' has no "
                        f"'## {number}.' heading"
                    )


def test_every_python_adr_reference_matches_exactly_one_file():
    adr_dir = REPO_ROOT / "docs" / "adr"
    for path in _python_files():
        for chunk in _documentary_chunks(path):
            for match in ADR_PATTERN.finditer(chunk):
                number = match.group(1)
                matches = sorted(adr_dir.glob(f"{number}-*.md"))
                assert len(matches) == 1, (
                    f"{path.relative_to(REPO_ROOT)} cites 'ADR {number}', "
                    f"which matches {len(matches)} files under "
                    "'docs/adr/', not exactly one"
                )


def test_every_design_section_citation_names_its_document():
    for path in _python_files():
        for chunk in _documentary_chunks(path):
            match = BARE_DESIGN_SECTION_PATTERN.search(chunk)
            assert not match, (
                f"{path.relative_to(REPO_ROOT)} cites {match.group(0)!r} "
                "without a versioned path -- ambiguous between the two "
                "design documents this project has"
            )


def test_no_python_documentary_reference_points_outside_the_repository():
    for path in _python_files():
        for chunk in _documentary_chunks(path):
            for pattern in (FORBIDDEN_POINTER_PATTERN, SESSIONS_PATTERN):
                match = pattern.search(chunk)
                assert not match, (
                    f"{path.relative_to(REPO_ROOT)} names "
                    f"{match.group(0)!r}, which points at a plan step or "
                    "a gitignored path that is not part of this repository"
                )
