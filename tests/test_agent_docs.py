"""Structural contract for the P1 agent-integration prose and skills.

These files are shipped user-facing content. The tests read them as data and do
not import package internals; runtime behavior is covered by black-box CLI tests.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs/reference/agent-integration.md"
ADOPTION = ROOT / "docs/adoption.md"
ADOPT_SKILL = ROOT / "skills/adopt-validated-memory/SKILL.md"
CONSULT_SKILL = ROOT / "skills/consult-project-memory/SKILL.md"
ASK_SKILL = ROOT / "skills/ask-validated-memory/SKILL.md"

PROFILE = """\
---
schema_version: 1
discovery: explicit
reliance: lightweight
---
"""

PROFILE_JSON = (
    '{"configured":true,"discovery":"explicit","host_support":'
    '{"delivery_verification":"not_checked","shipped":["claude-code"]},'
    '"operation":"profile","profile_path":"validated-memory-profile.md",'
    '"reliance":"lightweight","schema_version":1}'
)


def _text(path):
    return path.read_text(encoding="utf-8")


def _normalized(path):
    return " ".join(_text(path).split())


def test_reference_exists_and_carries_the_exact_profile_wire():
    text = _text(REFERENCE)
    normalized = _normalized(REFERENCE)
    assert PROFILE in text
    assert PROFILE_JSON in text
    assert "A missing profile" in normalized
    assert "configured` is false" in normalized
    assert "discovery is effectively `off`" in normalized
    assert "reliance is reported as `lightweight`" in normalized
    assert "delivery_verification" in text
    assert "does not say the installed host loaded or executed it" in normalized


def test_adoption_guide_and_skill_offer_two_independent_axes():
    for path in (ADOPTION, ADOPT_SKILL):
        text = _normalized(path)
        assert "Discovery:" in text
        assert "Reliance:" in text
        assert "automatic" in text
        assert "explicit" in text
        assert "lightweight" in text
        assert "reviewed" in text
        assert "using existing checked consultation when enrolled" in text
        assert "Busca en VA:" in text
        assert "Validated-memory:" in text
        assert "not an evidence state" in text
        assert "global gate" in text
    assert PROFILE in _text(ADOPTION)
    assert PROFILE in _text(ADOPT_SKILL)


def test_profile_authoring_is_safe_repeatable_and_narrow():
    text = _normalized(ADOPT_SKILL)
    for phrase in (
        "This is the only branch that authors `validated-memory-profile.md`",
        "never edits `validated-memory.md`",
        "preserve it and ask nothing",
        "Never replace a symlink, non-regular file, file over 8192 bytes",
        "retain its optional body byte for byte",
        "Recheck its identity and metadata immediately before replacement",
        "perform no write and report a no-op",
        "changes only `discovery` to `off`",
        "preserves reliance",
        "without asking for a second generic confirmation",
    ):
        assert phrase in text
    assert "Never interpolate either the user's prompt or profile text into a shell" in text
    assert "never source or execute the profile" in text


def test_discovery_is_not_presented_as_checked_use_or_task_scope():
    reference = _normalized(REFERENCE)
    consult = _normalized(CONSULT_SKILL)
    for phrase in (
        "does not validate a claim",
        "does not cover an agent's internal turns",
        "task-wide activation",
        "subagent inheritance",
        "automatic learning capture",
        "does not automatically issue a challenge",
        "preference changes candidate guidance, not enforcement",
        "does not uninstall the plugin or disable the three existing `SessionStart` hooks",
    ):
        assert phrase in reference
    assert "not a consultation receipt, checked-use record or completed review" in consult
    assert "never blocks an agent action" in consult


def test_reference_pins_activation_limits_privacy_and_outcomes():
    text = _normalized(REFERENCE)
    for phrase in (
        "at most 4096 UTF-8 bytes",
        "no larger than 65536 bytes",
        "at most 8192 bytes",
        "maximum of five results",
        "12288-byte recall budget",
        "three second timeout",
        "There is no stemming, synonym expansion, language translation",
        "No invocation",
        "No query",
        "No matches",
        "Unavailable",
        "Candidates",
        "never contains the raw query, conversation, profile body",
    ):
        assert phrase in text


def test_ask_skill_routes_integration_questions_without_claiming_host_proof():
    text = _normalized(ASK_SKILL)
    assert "prompt-discovery mode, profile status, exact prefixes, deactivation" in text
    assert "docs/reference/agent-integration.md" in text
    assert "python3 -P -m validated_memory agent profile" in text
    assert "does not prove that Claude Code ran the hook" in text
    assert "consult-project-memory" in text


def test_synthetic_example_is_isolated_and_exercises_both_activation_paths():
    text = _text(REFERENCE)
    normalized = _normalized(REFERENCE)
    assert 'demo_root="$(mktemp -d)"' in text
    assert 'cd "$demo_root"' in text
    assert "validated_memory init" in text
    assert '"prompt":"What did we learn about kb-042 timeout retries?"' in text
    assert '"prompt":"Validated-memory: kb-042 timeout retries"' in text
    assert "The ordinary prompt prints nothing" in normalized
    assert "Change `discovery: explicit` to `discovery: off`" in normalized
    assert "change it to `automatic`" in normalized
    assert "This direct CLI check proves adapter output, not host delivery" in normalized


def test_reference_links_the_separation_decision():
    assert "0021-agent-policy-is-separate-from-knowledge-evidence.md" in _text(REFERENCE)
