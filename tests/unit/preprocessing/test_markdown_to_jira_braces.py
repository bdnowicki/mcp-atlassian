"""Regression tests for PROPX-398 - write-path brace escaping.

Defect 1: ``markdown_to_jira`` never escaped ``{``, so any ``{...}`` pair in
user text was parsed by the Jira Server/DC wiki renderer as macro syntax.
Measured on Jira Server/DC (CHSTC-1101/1102, asserting on rendered HTML
fetched over raw REST, 2026-09-10): a single rendered ``<li>`` swallowed two
whole sections, an ``h2`` heading rendered as literal text, a three-item list
lost two items, and one prose paragraph was torn into three fragments.

The fix escapes only the OPENING brace: a lone ``}`` renders literally, and
escaping the closing brace as well collides with the ``}}`` monospace
terminator. Escaping is idempotent by lookbehind - double escaping (``\\\\{``)
is measured to tear the paragraph AND inject a forced newline.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from mcp_atlassian.preprocessing.jira import JiraPreprocessor


@pytest.fixture
def preprocessor():
    return JiraPreprocessor()


@pytest.fixture
def preprocessor_translation_disabled():
    return JiraPreprocessor(disable_translation=True)


def test_escaped_brace_in_prose(preprocessor):
    """Escaped prose braces render as one intact <p>; unescaped tear it apart."""
    # LIVE: the escaped form renders as a single <p>. The unescaped form
    # rendered '<p>before a</p>\n{b}\n<p>c after</p>' - the paragraph is torn
    # and {b} becomes a macro block.
    assert preprocessor.markdown_to_jira("before a{b}c after") == r"before a\{b}c after"


def test_escaped_brace_in_monospace_midspan(preprocessor):
    """A brace mid-monospace renders as a clean <tt>/issue/{id}/end</tt> span."""
    assert (
        preprocessor.markdown_to_jira("see `/issue/{id}/end` now")
        == r"see {{/issue/\{id}/end}} now"
    )


def test_escaped_brace_in_monospace_tail(preprocessor):
    """Monospace ending in '}' keeps ONE stray brace outside the <tt> - by design.

    LIVE: this renders '<tt>GET /rest/api/2/issue/{issueKey</tt>}'. Content
    ending in ``}`` collides with the ``}}`` monospace terminator and NO
    escaping fixes it: a trailing space kills the monospace entirely, and
    dropping the ``{{}}`` wrapper loses the <tt> span. The residual is Jira's
    renderer, not this converter, and it is COSMETIC ONLY - no macro block, no
    torn paragraph, no torn <li>. Do not "fix" this expectation to an
    idealised clean <tt>.
    """
    assert (
        preprocessor.markdown_to_jira("inline `GET /rest/api/2/issue/{issueKey}` here")
        == r"inline {{GET /rest/api/2/issue/\{issueKey}}} here"
    )


def test_no_double_escape_idempotent(preprocessor):
    """Input that already carries a brace escape is returned byte-identical.

    NEGATIVE CONTROL for the ``(?<!\\\\)`` lookbehind. LIVE: raw
    'before a\\\\{b\\\\}c after' renders a torn paragraph, an injected forced
    newline AND a '{b\\\\}' macro block, so double escaping is a correctness
    bug, not a cosmetic one. This test passes vacuously today and is the guard
    that fails if anyone drops the lookbehind.
    """
    assert (
        preprocessor.markdown_to_jira(r"before a\{b}c after") == r"before a\{b}c after"
    )


def test_fenced_code_braces_untouched(preprocessor):
    """Fenced code content keeps literal braces - it is behind a placeholder.

    NEGATIVE CONTROL, passes before and after the fix. LIVE: renders
    '<pre class="code-java">x = {id}</pre>' with the brace intact. Fenced
    content is extracted before the escape point and restored after it, so it
    is structurally unreachable; if this fails, the escape hook is in the
    wrong place. The missing newline after the opening macro is a pre-existing
    quirk - assert the current bytes, do not "fix" it here.
    """
    assert (
        preprocessor.markdown_to_jira("```java\nx = {id}\n```")
        == "{code:java}x = {id}\n{code}"
    )


def test_inline_code_no_braces_control(preprocessor):
    """Brace-free inline code is unchanged, delimiters included."""
    assert preprocessor.markdown_to_jira("`code`") == "{{code}}"


@pytest.mark.parametrize(
    "markdown, expected",
    [
        pytest.param(
            '<span style="color:#ff0000">a {x} b</span>',
            r"{color:#ff0000}a \{x} b{color}",
            id="hex-colour-escapes-content-only",
        ),
        pytest.param(
            '<span style="color:red">a</span>',
            "{color:red}a{color}",
            id="named-colour-round-trips",
        ),
    ],
)
def test_converter_emitted_delimiters_not_escaped(preprocessor, markdown, expected):
    """The escape reaches macro CONTENT but never the converter's own braces."""
    # The named-colour half also pins Part B3: the writer's colour pattern was
    # `(#[^"]+)`, so a named colour leaked through as
    # '[span style="color:red"]a[/span]'.
    assert preprocessor.markdown_to_jira(markdown) == expected


def test_heading_text_brace(preprocessor):
    """A brace in heading text is escaped; the visible heading text is correct.

    ACCEPTED RESIDUAL, and for this shape alone the escape is a net cost:
    Jira's own generated anchor id degrades from '<a name="Section">' to
    '<a name="Section%5C">', so an existing '#Section' deep link breaks.
    Re-measured on the renderer in repair round 1: the PRE-FIX render of
    'h2. Section {A}' is fully correct - 'Section {A}' as visible text and a
    clean anchor - so a standalone heading brace was never the damaging
    shape (the h2 that rendered as literal body text was swallowed by an
    unterminated macro opened in a preceding list item, not by its own
    brace).

    Kept anyway, deliberately: exempting heading text would mean deciding
    per context whether a user's '{...}' is content or markup, which is the
    ambiguity the escape exists to remove, and the visible heading text is
    correct in both states. Do not "fix" this by special-casing 'h1.'-'h6.'
    lines.
    """
    assert preprocessor.markdown_to_jira("## Section {A}") == r"h2. Section \{A}"


def test_bullet_list_with_escaped_monospace(preprocessor):
    """All three list items survive when a brace inside monospace is escaped.

    LIVE: the escaped form renders a <ul> with exactly three <li> and no <p>
    injected inside the first one. The unescaped form injected a macro block
    and a <p> into the first <li> and lost the other two items.
    """
    markdown = (
        "- item one with `GET /issue/{issueKey}`\n- item two plain\n- item three plain"
    )
    expected = (
        r"* item one with {{GET /issue/\{issueKey}}}"
        "\n* item two plain\n* item three plain"
    )
    assert preprocessor.markdown_to_jira(markdown) == expected


def test_brace_in_link_target(preprocessor):
    """A brace inside a link TARGET must be escaped before the URL is hidden.

    LIVE: the escaped form yields href='https://example.com/{env}' with the
    correct link text; the unescaped form tore the paragraph AND truncated the
    link at the brace. This is the case proving the escape must not live where
    MARKDOWNURL placeholders have already hidden link targets.
    """
    assert (
        preprocessor.markdown_to_jira("[cfg](https://example.com/{env})")
        == r"[cfg|https://example.com/\{env}]"
    )


def test_brace_in_table_cell(preprocessor):
    """A brace in a table cell is escaped and the row still converts."""
    markdown = "| A | B |\n|---|---|\n| {x} | y |"
    assert preprocessor.markdown_to_jira(markdown) == "||A||B||\n|" + r"\{x}" + "|y|"


def test_lone_closing_brace_untouched(preprocessor):
    """Only '{' is escaped - a lone '}' is left exactly as the user wrote it.

    LIVE: a lone '}' and a lone '{' both render literally; the renderer hazard
    needs a '{...}' PAIR. Escaping the lone opener anyway is accepted
    conservatism (a '\\{' renders as a literal '{'), and it keeps the escape a
    single-character rule with no lookahead over the rest of the line.
    """
    assert preprocessor.markdown_to_jira("a } b") == "a } b"
    assert preprocessor.markdown_to_jira("a { b") == r"a \{ b"


def test_disable_translation_passthrough(preprocessor_translation_disabled):
    """DISABLE_JIRA_MARKUP_TRANSLATION returns both directions byte-identical.

    Pins that the escape sits AFTER the early return, so the documented global
    passthrough stays a true passthrough.
    """
    text = "{{jira code}} and a bare {x} here"
    assert preprocessor_translation_disabled.markdown_to_jira(text) == text
    assert preprocessor_translation_disabled.jira_to_markdown(text) == text


def test_intraword_underscore_still_escaped(preprocessor):
    """The sibling underscore escape is unharmed: braces and '_' differ inside code.

    NEGATIVE CONTROL, passes before and after the fix. Underscores inside
    monospace must stay UNESCAPED (Jira does not italicise inside {{...}})
    even though braces inside monospace must be escaped.
    """
    assert (
        preprocessor.markdown_to_jira("the customfield_10101 here")
        == r"the customfield\_10101 here"
    )
    assert (
        preprocessor.markdown_to_jira("call `find_provider_by_url` now")
        == "call {{find_provider_by_url}} now"
    )


def test_this_regression_module_is_tracked_by_git():
    """These write-path cases must be COMMITTED with the source fix, not after it.

    PROPX-398 repair round 1 found this file, the read-path corruption file
    and the live render file all left UNTRACKED while
    src/mcp_atlassian/preprocessing/jira.py, preprocessing/base.py and
    jira/epics.py were modified in place. ``git commit -am ...`` - the most
    likely way this working tree gets committed - stages only tracked files,
    so it would ship a behavioural rewrite of the preprocessing regexes with
    ZERO of its regression tests, and plain ``git diff`` would show a reviewer
    that rewrite with nothing pinning it.

    That coupling is not cosmetic: the write-path brace escape and the
    read-path backslash-lookbehind guards must land in ONE commit, because an
    escape shipped without the guards turns a backslash-escaped macro into
    permanent deletion of the delimiters on the very next read. The tests that
    pin the coupling therefore cannot lag behind the source either.

    Fails while the file is only in the working tree; passes once it is in the
    index (``git ls-files`` reads the index, so ``git add`` is enough - no
    commit is required). Skips where there is no git work tree at all, e.g. an
    sdist or tarball checkout.
    """
    path = Path(__file__).resolve()
    git = shutil.which("git")
    if git is None:
        pytest.skip("git executable not available")

    inside = subprocess.run(
        [git, "rev-parse", "--is-inside-work-tree"],
        cwd=path.parent,
        capture_output=True,
        text=True,
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        pytest.skip("not inside a git work tree")

    tracked = subprocess.run(
        [git, "ls-files", "--error-unmatch", "--", str(path)],
        cwd=path.parent,
        capture_output=True,
        text=True,
    )
    assert tracked.returncode == 0, (
        f"{path.name} is not in the git index, so committing the PROPX-398 "
        "source fix would ship it without its regression tests. Run: "
        "git add tests/unit/preprocessing/test_markdown_to_jira_braces.py"
    )


# --------------------------------------------------------------------------
# Measured, ACCEPTED consequences of the write-path escape (repair round 1).
#
# Each case below records a render or pipeline difference that the brace
# escape deliberately accepts. They are tests, not comments, so the
# trade-off is a decision with a name rather than a surprise a reviewer
# finds later - and so nobody "improves" the escape by exempting a context.
# A per-context exemption would have to guess whether a `{...}` the user
# typed is content or markup, which is the ambiguity the escape removes.
# --------------------------------------------------------------------------


def test_bare_url_with_brace_is_escaped_accepted_regression(preprocessor):
    r"""A bare URL keeps the escape, and shows a backslash - accepted (P12).

    RENDERED, patched: '<p>go <a href="https://example.com/a/" ...>'
    'https://example.com/a/\</a>{env}/b now</p>' - ONE <p>, no macro block,
    the brace text survives in the paragraph, but a visible backslash appears
    inside the anchor TEXT.

    RENDERED, pre-fix: '<p>go <a href="https://example.com/a/" ...>'
    'https://example.com/a/</a></p>\n{env}\n<p>/b now</p>' - no backslash,
    but the paragraph is torn into three fragments with a macro block between
    them. The href is truncated at the brace in BOTH states.

    So this one narrow shape is cosmetically worse and structurally better,
    and the escape stays. Cause: a bare URL is prose to this converter, so it
    is left to Jira's own autolinker, which ends the link at the backslash and
    prints it.

    Re-measured on the renderer in repair round 1, an author who needs a brace
    in a URL has a clean workaround, and it is the shape this converter already
    emits for Markdown links and autolinks: BOTH
    '[https://example.com/a/\{env}/b]' and '[cfg|https://example.com/a/\{env}/b]'
    render 'href="https://example.com/a/{env}/b"' with no backslash anywhere.
    The same bracketed form UNESCAPED renders
    '<p>go [https://example.com/a/</p>' plus a '{env}' macro block, so the
    escape is required there too - only the bare form pays for it.
    """
    assert (
        preprocessor.markdown_to_jira("go https://example.com/a/{env}/b now")
        == r"go https://example.com/a/\{env}/b now"
    )
    # Contrast: the angle-bracket autolink becomes an explicit bracketed wiki
    # link, the form measured to render an escaped brace as a clean href.
    assert (
        preprocessor.markdown_to_jira("go <https://example.com/a/{env}/b> now")
        == r"go [https://example.com/a/\{env}/b] now"
    )


def test_user_typed_macro_syntax_follows_the_measured_macro_set(preprocessor):
    r"""Hand-written macro syntax is escaped UNLESS Jira actually installs it.

    REVISED in repair round 2 after measuring which macro names the
    reference instance really has (CHSTC-1101, raw wiki over REST,
    ``expand=renderedBody``: 38 names probed as ``AAA {name[:args]} BBB``).
    Round 1 escaped every brace and called the resulting literal text "the
    intent"; the measurement shows that is only half right, and the half it
    got wrong is a real regression on a read -> write cycle.

    * A name Jira does NOT install renders, unescaped, as a literal block
      that TEARS the paragraph - byte for byte the same damage as an unknown
      ``{b}``. So escaping it is the BETTER render, not a loss. MEASURED:
      ``see \{toc:maxLevel=2} here`` -> '<p>see {toc:maxLevel=2} here</p>',
      one intact paragraph, where the unescaped form splits it in three.
    * A name Jira DOES install is real markup, and escaping it destroys it.
      MEASURED: ``{color:red}x{color}`` -> '<font color="red">x</font>';
      escaped, the colour is gone for good.

    So the escape now exempts a complete pair of an installed macro, and the
    author's escape hatch is preserved in both directions: a literal
    ``\{color:red}`` typed by hand passes through untouched (the ``(?<!\\)``
    lookbehind) and is measured to render as literal text, and
    DISABLE_JIRA_MARKUP_TRANSLATION=true still bypasses the whole converter
    (``test_disable_translation_passthrough`` above).
    """
    # Installed macro, complete pair -> markup, kept verbatim.
    assert (
        preprocessor.markdown_to_jira("literal {color:red}x{color} typed by user")
        == "literal {color:red}x{color} typed by user"
    )
    # Not installed -> escaped, which is the better render.
    assert (
        preprocessor.markdown_to_jira("see {toc:maxLevel=2} here")
        == r"see \{toc:maxLevel=2} here"
    )
    # Installed name but an UNMATCHED delimiter -> prose, so still escaped.
    # Unescaped this is measured destructive: 'use {code} for code' opens a
    # code panel that swallows the rest of the document.
    assert (
        preprocessor.markdown_to_jira("use {code} for code") == r"use \{code} for code"
    )
    # The author's own escape survives, and renders literally.
    assert (
        preprocessor.markdown_to_jira(r"literal \{color:red}x\{color} typed")
        == r"literal \{color:red}x\{color} typed"
    )


def test_escape_reaches_the_caller_until_the_read_path_strips_it(preprocessor):
    r"""Stored fields now carry ``\{``; only the read path removes it.

    Measured consequence of the escape that the plan did not enumerate: the
    four tool responses that build models straight from the REST payload
    (jira_search, jira_update_issue, jira_create_issue,
    jira_batch_create_issues) never call ``_clean_text``, so they surface
    these backslashes verbatim to the caller. This test pins where the escape
    is removed - and where it therefore is not.
    """
    stored = preprocessor.markdown_to_jira("inline `GET /x/{k}` and bare {p} here")
    assert stored == r"inline {{GET /x/\{k}}} and bare \{p} here"
    # A read routed through _clean_text is clean...
    assert (
        preprocessor.clean_jira_text(stored) == "inline `GET /x/{k}` and bare {p} here"
    )
    # ...and the raw stored bytes, which the four bypassing paths return, are
    # not. Recorded so the noise is attributed to this change and not to a
    # later "corruption" report.
    assert r"\{" in stored


@pytest.mark.parametrize(
    "markdown, once, twice",
    [
        pytest.param(
            "inline `GET /x/{k}` here",
            r"inline {{GET /x/\{k}}} here",
            r"inline \{\{GET /x/\{k}}} here",
            id="inline-code-with-brace",
        ),
        pytest.param(
            "run `npm test` now",
            "run {{npm test}} now",
            r"run \{\{npm test}} now",
            id="inline-code-without-brace",
        ),
    ],
)
def test_write_path_is_not_idempotent_after_the_escape(
    preprocessor, markdown, once, twice
):
    """Applying the writer to its OWN output now damages inline code.

    Pre-fix, a second application was a no-op for inline code; now the ``{{``
    the converter emitted is itself escaped and the monospace span is lost.
    ``markdown_to_jira`` was already non-idempotent for bold, headings, lists
    and tables and is documented Markdown -> wiki only, so this is a drift
    record, not a supported shape: writer output must be read back through
    ``clean_jira_text`` first. Deliberately NOT guarded - a "skip braces that
    look like a known macro" exception reintroduces exactly the ambiguity the
    escape exists to remove.
    """
    assert preprocessor.markdown_to_jira(markdown) == once
    assert preprocessor.markdown_to_jira(once) == twice
    assert twice != once
    # The damage is bounded: the lookbehind makes a third pass a fixed point.
    assert preprocessor.markdown_to_jira(twice) == twice
    # And the SUPPORTED pipeline - read, then write - is a fixed point.
    assert preprocessor.markdown_to_jira(preprocessor.clean_jira_text(once)) == once
