"""PROPX-398 repair round 1 - read -> write must not destroy Jira markup.

The write-path brace escape (defect 1) protects a ``{...}`` the AUTHOR typed
from being macro-parsed.  Verification then found the other half: anything
``jira_to_markdown`` leaves RAW in its "Markdown" output is indistinguishable
from author text by the time the writer sees it, so the escape flattens it.
For ``{code}``/``{noformat}`` that was measured, on rendered HTML, as total
destruction of a real block, and it was a NEW regression - the same
read -> write was byte-identical before the escape existed.

Measured on Jira Server/DC (CHSTC-1101, raw REST, ``expand=renderedBody``,
scratch comments deleted after reading), 2026-09-10::

    {code:java|title=Example}\\nint x = 1;\\n{code}
      -> <div class="code panel">
           <div class="codeHeader panelHeader"><b>Example</b></div>
           <pre class="code-java"><span class="code-object">int</span> x = 1;

    \\{code:java|title=Example}\\nint x = 1;\\n\\{code}      (the write-back)
      -> <p>{code:java|title=Example}<br/>int x = 1;<br/>{code}</p>

Panel, header and syntax highlighting all gone.  The fix widens the READER
so the real shapes are extracted - and therefore placeholdered, so the
escape can never reach them - and carries the macro parameters through the
Markdown fence info string so the write-back rebuilds the block intact.

This module also pins the two verification findings whose suggested remedies
were measured WRONG (image targets) or whose cause was misdiagnosed (list
indent doubling), and records the limitations that are deliberately left
open, so each one is a decision rather than something a later reader
rediscovers.
"""

import pytest

from mcp_atlassian.preprocessing.jira import JiraPreprocessor


@pytest.fixture
def preprocessor():
    return JiraPreprocessor()


def round_trip(preprocessor: JiraPreprocessor, stored: str) -> str:
    """The realistic agent flow: jira_get_issue -> edit -> jira_update_issue."""
    return preprocessor.markdown_to_jira(preprocessor.clean_jira_text(stored))


# --------------------------------------------------------------------------
# Finding 1 - parameterised / non-lowercase {code} and {noformat} blocks
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored, expected",
    [
        # The measured case.  The header is only preserved because the
        # reader carries '|title=Example' through the fence info string.
        pytest.param(
            "{code:java|title=Example}\nint x = 1;\n{code}",
            "{code:java|title=Example}int x = 1;\n{code}",
            id="language-and-title",
        ),
        # Any uppercase letter in the language defeated '[a-z]+'.
        # Normalising it to lowercase is render-neutral: '{code:Java}' and
        # '{code:java}' both render '<pre class="code-java">'.
        pytest.param(
            "{code:Java}\nint x;\n{code}",
            "{code:java}int x;\n{code}",
            id="uppercase-language",
        ),
        # '+' and '#' are legal in a Jira language and both are SUPPORTED
        # on the reference instance ('<pre class="code-c++">').
        pytest.param(
            "{code:c++}\nif (x) { y+z; }\n{code}",
            "{code:c++}if (x) { y+z; }\n{code}",
            id="cpp-language-with-braces-in-body",
        ),
        pytest.param(
            "{code:c#}\nvar d = 1;\n{code}",
            "{code:c#}var d = 1;\n{code}",
            id="csharp-language",
        ),
        # PRE-EXISTING, UNRELATED defect surfaced while measuring this:
        # 'objective-c' is in this repo's VALID_JIRA_LANGUAGES but the
        # reference instance rejects it (its list has 'objc'), so
        # '{code:objective-c}' renders an 'Unable to find source-code
        # formatter for language: objective-c' error div - above a plain
        # <pre> that still holds the code, so it is ugly, not destructive.
        # Reproducing the stored bytes faithfully is still correct here: the
        # author's value is what it is, and silently rewriting a language is
        # not this converter's call. Fixing VALID_JIRA_LANGUAGES is a
        # separate change to a different constant.
        pytest.param(
            "{code:objective-c}\nid x;\n{code}",
            "{code:objective-c}id x;\n{code}",
            id="hyphenated-language",
        ),
        # A parameter sitting in the language slot must be passed through
        # verbatim, not guessed at as a language: '{code:borderStyle=solid}'
        # renders a panel with 'border-style: solid'.
        pytest.param(
            "{code:borderStyle=solid}\nb\n{code}",
            "{code:borderStyle=solid}b\n{code}",
            id="parameter-in-language-slot",
        ),
        # '{code|title=T}' is NOT actually a valid opener - measured, it
        # renders as literal text plus an EMPTY code panel - but the reader
        # still has to reproduce it byte for byte rather than "improve" it,
        # because the stored value is what the author has.
        pytest.param(
            "{code|title=T}\nplain\n{code}",
            "{code|title=T}plain\n{code}",
            id="pipe-separated-parameter-no-language",
        ),
        # {noformat} is round-tripped as a fence whose info string is the
        # literal token 'noformat' (Jira has no such language, so it cannot
        # collide).  Without that the block came back as a {code} panel,
        # which is a different render: 'nopanel=true' produces a bare
        # <pre> while {code} always draws the panel chrome.
        pytest.param(
            "{noformat:nopanel=true}\nraw text\n{noformat}",
            "{noformat:nopanel=true}raw text\n{noformat}",
            id="noformat-with-parameters",
        ),
        pytest.param(
            "{noformat}\nraw\n{noformat}",
            "{noformat}raw\n{noformat}",
            id="noformat-plain",
        ),
        # The macro NAME is case-insensitive on the renderer: '{CODE}' and
        # '{Code}' both render a real code panel, so the reader must accept
        # them.  (Verified before widening the pattern - had Jira treated
        # them as literal text, extracting them would have turned prose
        # into a code block.)
        pytest.param(
            "{CODE}\nx = 1;\n{CODE}",
            "{code}x = 1;\n{code}",
            id="uppercase-macro-name",
        ),
        pytest.param(
            "{NOFORMAT}\nraw\n{NOFORMAT}",
            "{noformat}raw\n{noformat}",
            id="uppercase-noformat-name",
        ),
    ],
)
def test_macro_block_survives_read_then_write(preprocessor, stored, expected):
    """Every real {code}/{noformat} shape round-trips as a REAL macro block.

    Before the fix each of these was left raw by the reader and the writer
    then escaped its braces, so the write-back rendered as a literal
    paragraph. Asserting the exact bytes also pins that the language and the
    parameters survive, which is what keeps the panel header and the syntax
    highlighting.

    The single byte of drift - no newline after the opening macro - is the
    pre-existing fence asymmetry documented in the write-path brace tests,
    and it is measured render-neutral: '{code:java|title=Example}int x = 1;'
    and '{code:java|title=Example}\\nint x = 1;' produce byte-identical
    rendered HTML.
    """
    result = round_trip(preprocessor, stored)
    assert result == expected
    assert "\\{" not in result, "a real macro block must never be escaped"


def test_macro_block_read_write_is_bounded(preprocessor):
    """Repeated read/write cycles must converge, not accumulate.

    Two separate unbounded growths met here. The reader added a blank line
    inside the fence on every cycle (three blank lines inside <pre> after
    two cycles, and the renderer shows them), and an unextracted block grew
    two escape characters per cycle. Both are now fixed points after the
    first cycle, so an agent editing the same description repeatedly cannot
    degrade it without limit.
    """
    stored = "{code:java}\nif (x) { y(); }\n{code}"
    first = round_trip(preprocessor, stored)
    second = round_trip(preprocessor, first)
    third = round_trip(preprocessor, second)
    assert first == "{code:java}if (x) { y(); }\n{code}"
    assert second == first
    assert third == first


@pytest.mark.parametrize(
    "stored, kept",
    [
        pytest.param(
            "{code:java}\nMap<String,String> m;\n{code}",
            "Map<String,String> m;",
            id="generic-type-parameters",
        ),
        pytest.param(
            "{code:xml|title=Config}\n<a>1</a>\n{code}",
            "<a>1</a>",
            id="xml-body",
        ),
    ],
)
def test_macro_block_body_is_not_html_stripped(preprocessor, stored, kept):
    """An extracted block's body never reaches markdownify.

    Compounding the finding: an unextracted block's body also went through
    the markdownify stage, which silently DELETES anything that looks like a
    tag - 'Map<String,String> m' came back as 'Map m'. That is pre-existing
    behaviour for prose, but it was only reachable for code because the
    block had not been extracted.
    """
    assert kept in preprocessor.clean_jira_text(stored)
    assert kept in round_trip(preprocessor, stored)


@pytest.mark.parametrize(
    "wiki",
    [
        pytest.param("{Code Review} note", id="macro-name-is-a-prefix-of-words"),
        pytest.param("{codeblock}x{codeblock}", id="longer-macro-name"),
        pytest.param("a {coded} b", id="macro-name-with-suffix"),
    ],
)
def test_case_insensitive_macro_name_does_not_over_match(preprocessor, wiki):
    """NEGATIVE CONTROL for the widened, case-insensitive macro name.

    ``{code}`` must be followed by ``}``, ``:`` or ``|``; a token that
    merely STARTS with those letters is prose and must not be swallowed
    into a code block.
    """
    assert preprocessor.jira_to_markdown(wiki) == wiki


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("CODE", id="upper-code"),
        pytest.param("Code", id="mixed-code"),
        pytest.param("code", id="lower-code"),
        pytest.param("cOdE", id="ragged-code"),
    ],
)
def test_monospace_payload_does_not_open_a_code_block(preprocessor, payload):
    """REGRESSION, repair round 2 - ``{{CODE}}`` must not act as a ``{code}`` opener.

    A ``{{...}}`` monospace span ends in ``}}``, so its payload plus that
    terminator is byte for byte a block-macro delimiter: ``{{CODE}}``
    contains ``{CODE}`` starting at the span's SECOND brace. Making the
    block-macro name case-insensitive (needed for ``{Code:java}``) extended
    a pre-existing lowercase-only collision to every case variant.

    Proved against a detached worktree at the branch point (22369b7) versus
    the working tree on identical input. At 22369b7, ``{{CODE}}`` was
    handled correctly because the pattern was lowercase-only; after the
    widening it produced::

        a {```\\n} b\\n{code:java}\\nint x;\\n```

    - the REAL ``{code:java}`` block swallowed into a bogus fence and the
    monospace opener orphaned. End to end that broke the round-trip fixed
    point the whole change depends on, and RENDERED on CHSTC-1101 it
    silently destroyed the author's letter case: the first write rendered
    ``<p>the <tt>CODE</tt> constant</p>`` plus an intact java panel, the
    second ``<p>the <tt>code</tt> constant</p>`` and nothing else.

    The fix is a second, SEPARATE ``(?<!\\{)`` lookbehind on both the opener
    and the closer of both block patterns, so the span is left to the
    monospace extractor that runs after them. It must NOT be merged with
    the backslash guard into ``(?<![\\\\{])`` - that drops the backslash
    guard and re-breaks ``test_escaped_macro_literal_survives``.
    """
    wiki = f"a {{{{{payload}}}}} b\n{{code:java}}\nint x;\n{{code}}"
    assert preprocessor.jira_to_markdown(wiki) == (
        f"a `{payload}` b\n```java\nint x;\n```"
    )


def test_monospace_payload_does_not_open_a_noformat_block(preprocessor):
    """The same collision on ``{noformat}``; both patterns carry both guards."""
    wiki = "a {{NOFORMAT}} b\n{noformat}\nx\n{noformat}"
    assert preprocessor.jira_to_markdown(wiki) == (
        "a `NOFORMAT` b\n```noformat\nx\n```"
    )


def test_monospace_payload_collision_keeps_the_fixpoint(preprocessor):
    """The end-to-end consequence: ``{{CODE}}`` must reach a fixed point.

    This is the assertion the unit suite was missing - it passed either way
    before, which is why the regression shipped.
    """
    source = "the `CODE` constant\n\n```java\nint x;\n```"
    first = preprocessor.markdown_to_jira(source)
    read_back = preprocessor.clean_jira_text(first)
    assert first == "the {{CODE}} constant\n\n{code:java}int x;\n{code}"
    assert read_back == source
    assert preprocessor.markdown_to_jira(read_back) == first


# --------------------------------------------------------------------------
# Finding 6 - the fence info string on the WRITE path
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "markdown, expected",
    [
        pytest.param(
            "```c++\nint x = {1};\n```",
            "{code:c++}int x = {1};\n{code}",
            id="cpp",
        ),
        pytest.param(
            "```c#\nvar d = new Dict<int>();\n```",
            "{code:c#}var d = new Dict<int>();\n{code}",
            id="csharp",
        ),
        pytest.param(
            "```objective-c\nid x = {1};\n```",
            "{code:objective-c}id x = {1};\n{code}",
            id="hyphenated",
        ),
        # An info string Jira has no language for still has to be
        # EXTRACTED - that is what puts the body out of the escape's reach.
        # The language is then dropped, exactly as for any unknown language.
        pytest.param(
            "```java title\nint x = {1};\n```",
            "{code}int x = {1};\n{code}",
            id="multi-word-info-string",
        ),
        # NEGATIVE CONTROL: the ordinary case is unchanged.
        pytest.param(
            "```java\nx = {id}\n```",
            "{code:java}x = {id}\n{code}",
            id="plain-language-unchanged",
        ),
    ],
)
def test_fence_info_string_is_not_word_characters_only(
    preprocessor, markdown, expected
):
    """A fence the extractor refuses has its body brace-escaped - and worse.

    The writer's fence pattern was ``' ```(\\w*)\\n '``, so ``c++``, ``c#``,
    ``objective-c`` and any multi-word info string were never extracted.
    The body was then brace-escaped AND the leftover backtick pair was
    re-captured as INLINE code, so ``' ```c#\\nx = {1};\\n``` '`` became
    ``' ``{{c#\\nx = \\{1};\\n}}`` '``. Rendered, that produced no code block
    at all and turned ``(x)`` into an error emoticon image, while the
    equivalent ``{code:c++}`` is a fully supported language on the
    reference instance.
    """
    assert preprocessor.markdown_to_jira(markdown) == expected


def test_non_word_fence_does_not_accumulate_escapes(preprocessor):
    """The refused fence also grew two braces on every cycle - unbounded.

    Measured before the fix: ``w1='``{{c#...}}``'``,
    ``w2='`{{\\{\\{c#...}}}}`'``, ``w3='{{\\{\\{\\{\\{c#...}}}}}}'``.
    """
    first = preprocessor.markdown_to_jira("```c#\nvar d = new Dict<int>();\n```")
    second = round_trip(preprocessor, first)
    third = round_trip(preprocessor, second)
    assert first == "{code:c#}var d = new Dict<int>();\n{code}"
    assert second == first
    assert third == first


# --------------------------------------------------------------------------
# Finding 4 - "scheme" must mean a scheme, not "a word and a colon"
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wiki",
    [
        pytest.param("TODO [Note: check this] later", id="note-prefix"),
        pytest.param("[TODO: fix]", id="todo-prefix"),
        pytest.param("[WARNING: bad]", id="warning-prefix"),
        pytest.param("[CHSTC-1101: summary]", id="issue-key-prefix"),
        pytest.param("[ISO: 8601]", id="iso-prefix"),
        pytest.param("[x: y]", id="single-letter-prefix"),
        pytest.param("[a:b]", id="no-space-after-colon"),
        pytest.param(r"[C:\temp\f.txt]", id="windows-drive-letter"),
        pytest.param("[12:30]", id="clock-time"),
        pytest.param("[https://x.example/a b] spaced", id="space-in-target"),
    ],
)
def test_bracketed_prose_keeps_its_brackets(preprocessor, wiki):
    """The bare-link rule must not unwrap anything but a real URL.

    ``'(?<![!\\w\\]])\\[([A-Za-z][A-Za-z0-9+.-]*:[^\\]\\n]+)\\](?!\\()'`` treated
    any word followed by a colon as a scheme, so ``[Note: check this]``,
    ``[TODO: fix]`` and ``[C:\\temp\\f.txt]`` all lost their brackets - the
    exact silent rewrite of the author's text that replacing the old
    indiscriminate stripper was supposed to stop. It was also internally
    inconsistent: ``[12:30]`` kept its brackets only because it starts with
    a digit.

    LIVE: ``[Note: check this]`` renders ``<span class="error">&#91;Note:
    check this&#93;</span>`` - byte for byte the same shape as ``[WIP]``,
    which the read-path contract deliberately PRESERVES. Same input class,
    so it must get the same treatment.
    """
    assert preprocessor.jira_to_markdown(wiki) == wiki


@pytest.mark.parametrize(
    "wiki, expected",
    [
        pytest.param(
            "see [https://x.example/a] now",
            "see https://x.example/a now",
            id="https",
        ),
        pytest.param(
            "mail [mailto:a@b.com] now",
            "mail mailto:a@b.com now",
            id="mailto",
        ),
        pytest.param(
            "see [file:///tmp/x] now",
            "see file:///tmp/x now",
            id="file",
        ),
        pytest.param(
            "call [tel:+123] now",
            "call tel:+123 now",
            id="tel",
        ),
        pytest.param(
            "see [FILE://x/y] now",
            "see FILE://x/y now",
            id="uppercase-scheme",
        ),
    ],
)
def test_real_scheme_urls_are_still_unwrapped(preprocessor, wiki, expected):
    """NEGATIVE CONTROL: narrowing the rule must not disable it.

    LIVE: a bracketed and a bare URL render the identical link, so
    unwrapping is safe and yields cleaner Markdown. ``//`` is required, plus
    an allowlist for the schemeless-authority schemes Jira autolinks.
    """
    assert preprocessor.jira_to_markdown(wiki) == expected


# --------------------------------------------------------------------------
# Finding 7 - the reader must strip only the escape the writer authors
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source, first_write, read_back",
    [
        pytest.param(
            r"latex `\{x\}` inline",
            r"latex {{\{x\}}} inline",
            r"latex `{x\}` inline",
            id="latex-braces",
        ),
        pytest.param(
            r"shell `echo \{a,b\}` inline",
            r"shell {{echo \{a,b\}}} inline",
            r"shell `echo {a,b\}` inline",
            id="shell-brace-expansion",
        ),
    ],
)
def test_backslash_in_code_content_keeps_the_fixpoint(
    preprocessor, source, first_write, read_back
):
    """A user's own backslash inside a code span is CONTENT, not an escape.

    The brace-unescape applied to monospace content stripped ``\\}`` as well
    as ``\\{``, although :func:`_escape_wiki_braces` only ever emits ``\\{``.
    That deleted a backslash the user wrote and broke the
    write -> read -> write fixed point the whole change rests on::

        'latex `\\{x\\}` inline'  ->  'latex {{\\{x\\}}} inline'
                                  ->  'latex `{x}` inline'   (both gone)
                                  ->  'latex {{\\{x}}} inline'  != w1

    Both shapes converged before the write-path escape existed, so this was
    a new regression. The rule is now uniform: strip exactly what the writer
    could have authored in that context - and inside a ``{code}`` block,
    where the writer escapes nothing, nothing is stripped.
    """
    assert preprocessor.markdown_to_jira(source) == first_write
    assert preprocessor.clean_jira_text(first_write) == read_back
    assert preprocessor.markdown_to_jira(read_back) == first_write


def test_hand_authored_monospace_escape_round_trips_byte_identically(preprocessor):
    """Hand-written Jira monospace with both escapes must not drift.

    Measured: stored '{{\\{x\\}}}' wrote back as '{{\\{x}}}'. Both render
    identically ('<p>{<tt>x</tt>}</p>', measured on the renderer), so the
    damage is to the stored bytes and to the fixpoint, not to the render -
    which is exactly why only a fixpoint test catches it.
    """
    stored = r"{{\{x\}}}"
    assert round_trip(preprocessor, stored) == stored


def test_writer_authored_escape_is_still_stripped_on_read(preprocessor):
    """NEGATIVE CONTROL: narrowing the unescape must not leak wiki syntax.

    The write path emits ``{{GET /x/\\{k} tail}}`` for the Markdown
    ``` `GET /x/{k} tail` ```, so the reader still has to remove that ``\\{``
    or raw wiki leaks into a field documented as Markdown.
    """
    assert preprocessor.jira_to_markdown(r"{{GET /x/\{k} tail}}") == (
        "`GET /x/{k} tail`"
    )
    assert preprocessor.markdown_to_jira("`GET /x/{k} tail`") == (
        r"{{GET /x/\{k} tail}}"
    )


@pytest.mark.parametrize(
    "wiki, expected",
    [
        pytest.param(r"a \{k} b", "a {k} b", id="opening-brace-only"),
        pytest.param(r"a \{k\} b", "a {k} b", id="both-braces"),
    ],
)
def test_prose_still_drops_both_brace_escapes(preprocessor, wiki, expected):
    """NEGATIVE CONTROL: PROSE keeps the wider rule, deliberately.

    In running text the renderer honours ``\\}`` as an escape too, so a
    hand-authored ``\\{k\\}`` is the literal ``{k}`` and reporting the
    backslashes would surface wiki noise. Only CODE content narrowed to
    ``\\{``, because there a backslash is content.
    """
    assert preprocessor.jira_to_markdown(wiki) == expected


def test_code_block_content_keeps_both_escapes_verbatim(preprocessor):
    """A {code} body is verbatim: the writer never escapes inside one."""
    assert preprocessor.clean_jira_text("{code}\\{x\\} here\n{code}") == (
        "```\n\\{x\\} here\n```"
    )


# --------------------------------------------------------------------------
# Finding 5 - REJECTED: exempting image targets is measurably worse
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "markdown, expected",
    [
        pytest.param(
            "![img](https://example.com/a/{v}.png)",
            "!https://example.com/a/%7Bv%7D.png|alt=img!",
            id="url-with-alt",
        ),
        pytest.param(
            "![](https://example.com/a/{v}.png)",
            "!https://example.com/a/%7Bv%7D.png!",
            id="url-without-alt",
        ),
        pytest.param(
            "![a](https://h/i.png?a={w})",
            "!https://h/i.png?a=%7Bw%7D|alt=a!",
            id="url-in-query-string",
        ),
        pytest.param(
            "![img](scr{1}.png)",
            r"!scr\{1}.png|alt=img!",
            id="attachment-keeps-the-escape",
        ),
    ],
)
def test_image_url_braces_are_percent_encoded(preprocessor, markdown, expected):
    """An absolute image URL is percent-encoded; an attachment keeps the escape.

    Three options were measured on rendered HTML, not two. Round 1 saw only
    the first two and picked the lesser evil::

        !https://example.com/a/{v}.png|alt=img!       (exempt from escaping)
          -> '<p>!https://example.com/a/</p>\\n{v}\\n<p>.png|alt=img!</p>'
             paragraph torn into three, {v} a macro block, NO <img> at all

        !https://example.com/a/\\{v}.png|alt=img!      (escaped)
          -> '<img src="https://example.com/a/\\{v}.png" alt="img" />'
             a real <img>, src wrong by one character

        !https://example.com/a/%7Bv%7D.png|alt=img!   (percent-encoded)
          -> '<img src="https://example.com/a/%7Bv%7D.png" alt="img" />'
             a real <img> and a correct, resolving src

    The third needs no exemption - ``%7B`` cannot re-open the macro hole -
    and it is canonicalisation rather than an invented rewrite, because
    ``{`` and ``}`` are excluded from every RFC 3986 URI production.
    Confirmed patched, end to end, on CHSTC-1101:
    ``markdown_to_jira('![a](https://h/i.png?a={w})')`` renders
    ``<img src="https://h/i.png?a=%7Bw%7D" alt="a" />``.

    An ATTACHMENT target is excluded on purpose: it is a filename Jira looks
    up literally, so percent-encoding it would stop the lookup ever finding
    an attachment genuinely named ``scr{1}.png``. Those keep the documented
    backslash escape, which is still better than the unescaped form (that
    loses the image entirely).

    Do not "fix" this test by exempting image targets from the escape - that
    trades a one-character URL error for a destroyed image and a torn
    paragraph, which is the option already measured worst.
    """
    assert preprocessor.markdown_to_jira(markdown) == expected


def test_image_url_percent_encoding_is_idempotent(preprocessor):
    """``%7B`` holds no brace, so a second pass cannot double-encode it."""
    once = preprocessor.markdown_to_jira("![a](https://h/i.png?a={w})")
    assert once == "!https://h/i.png?a=%7Bw%7D|alt=a!"
    # Already-encoded Markdown is a fixed point through the writer.
    assert preprocessor.markdown_to_jira("![a](https://h/i.png?a=%7Bw%7D)") == once
    # And the Markdown round trip stays clean.
    assert preprocessor.jira_to_markdown(once) == "![a](https://h/i.png?a=%7Bw%7D)"


def test_link_targets_render_clean_which_is_why_they_are_escaped(preprocessor):
    """NEGATIVE CONTROL for the finding above: a LINK target is unescaped by Jira.

    LIVE: '[cfg|https://example.com/a/\\{env}/b]' renders
    href="https://example.com/a/{env}/b" - clean. Link and image targets
    genuinely differ, and both are escaped from the same call site.
    """
    assert preprocessor.markdown_to_jira("[cfg](https://example.com/a/{env}/b)") == (
        r"[cfg|https://example.com/a/\{env}/b]"
    )


# --------------------------------------------------------------------------
# Findings 2, 3, 8, 9 - limitations recorded as decisions
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored, expected",
    [
        pytest.param("{toc:maxLevel=2}", r"\{toc:maxLevel=2}", id="toc"),
        pytest.param("{jira:key=ABC-1}", r"\{jira:key=ABC-1}", id="jira"),
        pytest.param(
            "{section}{column:width=50%}left{column}{section}",
            r"\{section}\{column:width=50%}left\{column}\{section}",
            id="section-column",
        ),
        pytest.param("a {unknownmacro} b", r"a \{unknownmacro} b", id="unknown"),
    ],
)
def test_uninstalled_macro_names_are_escaped_because_that_renders_better(
    preprocessor, stored, expected
):
    """Escaping an UNINSTALLED macro name is the better render, not a loss.

    Round 1 recorded this whole class as a limitation. Repair round 2
    measured it instead, and the measurement splits the class in two.
    Thirty-eight candidate macro names were rendered on CHSTC-1101 inside a
    paragraph as ``AAA {name[:args]} BBB`` (raw wiki over REST,
    ``expand=renderedBody``). Only ``anchor``, ``code``, ``color``,
    ``loremipsum``, ``noformat``, ``panel`` and ``quote`` produced real
    markup. Every name in this test - and ``status``, ``expand``,
    ``section``, ``column``, ``align``, ``children``, ``info``, ``note``,
    ``tip``, ``warning``, ``include``, ``html``, ``div``, ``span``,
    ``gallery``, ``chart``, ``contentbylabel``, ``tasklist``,
    ``table-plus``, ``card``, ``deck``, ``center``, ``thumbnail``,
    ``cache``, ``cheese`` - is NOT installed and renders UNESCAPED as a
    literal block that TEARS the enclosing paragraph, exactly as an unknown
    ``{b}`` does.

    So for these names the escape is a genuine improvement. MEASURED,
    patched: ``see \\{toc:maxLevel=2} here`` renders
    ``<p>see {toc:maxLevel=2} here</p>`` - one intact paragraph holding the
    literal text - where the unescaped form renders
    ``<p>see </p>\\n{toc:maxLevel=2}\\n<p> here</p>``.

    The other half of the class - installed macros, which the escape really
    did destroy - is fixed and pinned by
    ``test_installed_macros_survive_the_writer`` below. Do not "restore"
    these names to it: passing them through re-tears the paragraph.
    """
    assert round_trip(preprocessor, stored) == expected


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param("see {anchor:top} here", id="anchor-inline"),
        pytest.param("{anchor:top} jump [#top] now", id="anchor-with-link"),
    ],
)
def test_installed_inline_macro_survives_the_round_trip(preprocessor, stored):
    """PROPX-398 repair round 2 - the read -> write cycle must not eat an anchor.

    ``{anchor:...}`` IS installed on the reference instance, and it is the
    case that proves the round-1 escape was too broad. MEASURED on
    CHSTC-1101::

        see {anchor:top} here     -> '<p>see <a name="top"></a> here</p>'
        see \\{anchor:top} here    -> '<p>see {anchor:top} here</p>'

    So one read -> write cycle through round-1's code turned a working
    anchor into literal text and permanently broke every ``#top`` deep link
    pointing at it. ``anchor`` is exempted UNCONDITIONALLY rather than as a
    pair because it renders inline and self-closing: a lone occurrence is
    measured harmless, unlike the block macros below.
    """
    assert round_trip(preprocessor, stored) == stored
    assert "\\{" not in round_trip(preprocessor, stored)


@pytest.mark.parametrize(
    "raw_wiki",
    [
        pytest.param("{code:java}\nint x = 1;\n{code}", id="code-block"),
        pytest.param("{quote}q{quote}", id="quote"),
        pytest.param("{color:red}r{color}", id="colour"),
        pytest.param("{panel:title=T}b{panel}", id="panel"),
        pytest.param("{noformat}n{noformat}", id="noformat"),
        pytest.param("{anchor:top}", id="anchor"),
        pytest.param(
            "h3. T\n{anchor:top}\n{panel:title=N}body{panel}\n{code:java}int x;{code}",
            id="whole-stored-description",
        ),
    ],
)
def test_installed_macros_survive_the_writer(preprocessor, raw_wiki):
    """PROPX-398 repair round 2 - raw wiki fed to the writer keeps its macros.

    THE REACHABILITY IS CONFIRMED IN CODE, not assumed:
    ``JiraIssue.from_api_response`` returns ``description`` with no
    ``_clean_text`` call, so ``jira_search``, ``jira_create_issue``,
    ``jira_update_issue`` and ``jira_batch_create_issues`` hand an agent RAW
    wiki for the same field ``jira_get_issue`` returns as Markdown. An agent
    that reads a description from one of those, edits a word and posts it
    back through ``jira_update_issue`` used to destroy the whole document's
    markup in a single call.

    Round 1 pinned that as an accepted limitation. It is not acceptable, and
    the render measurement is unambiguous - the last case, a realistic
    stored description, on CHSTC-1101::

        h3. T\\n{anchor:top}\\n{panel:title=N}body{panel}\\n{code:java}int x;{code}
          -> <h3><a name="T"></a>T</h3>
             <p><a name="top"></a></p>
             <div class="panel"><div class="panelHeader"><b>N</b></div>
               <div class="panelContent"><p>body</p></div></div>
             <div class="code panel"><pre class="code-java">int x;</pre></div>

        the same text escaped by round 1's writer
          -> <h3><a name="T"></a>T</h3>
             <p>{anchor:top}<br/>{panel:title=N}body{panel}<br/>
                {code:java}int x;{code}<br/></p>

    Anchor, panel, panel header and syntax-highlighted code block all gone,
    in one PUT, with no error and a stored field that compares equal to what
    the caller sent. So the escape now exempts a COMPLETE PAIR of an
    installed macro (and ``{anchor:...}`` unconditionally, since it is
    inline and self-closing).

    ``markdown_to_jira`` still documents Markdown as its input and
    ``markdown_to_jira(clean_jira_text(x))`` is still the supported
    composition - routing those four responses through ``_clean_text``
    remains the real fix and is tracked separately. This test is the
    mitigation: violating the contract is no longer destructive for the
    macro shapes Jira actually renders.
    """
    assert preprocessor.markdown_to_jira(raw_wiki) == raw_wiki


@pytest.mark.parametrize(
    "raw_wiki, expected",
    [
        pytest.param(r"{{mono}}", r"\{\{mono}}", id="monospace-is-still-escaped"),
        pytest.param(
            "use {code} for code",
            r"use \{code} for code",
            id="lone-code-mention",
        ),
        pytest.param(
            "use {panel} in prose",
            r"use \{panel} in prose",
            id="lone-panel-mention",
        ),
        pytest.param(
            "use {quote} in prose",
            r"use \{quote} in prose",
            id="lone-quote-mention",
        ),
        pytest.param(
            "use {noformat} in prose",
            r"use \{noformat} in prose",
            id="lone-noformat-mention",
        ),
        pytest.param(
            "use {color:red} in prose",
            r"use \{color:red} in prose",
            id="lone-colour-mention",
        ),
        pytest.param(
            "{code:java}x{code} and a stray {code} mention",
            "{code:java}x{code} and a stray " + r"\{code}" + " mention",
            id="odd-trailing-occurrence-is-prose",
        ),
    ],
)
def test_unmatched_macro_delimiters_are_still_escaped(preprocessor, raw_wiki, expected):
    """The PAIRING requirement is what keeps the exemption evidence-based.

    Without it the exemption degrades into the "skip braces that look like a
    macro" guess the plan rejected, and that guess is measurably destructive
    for a lone delimiter. MEASURED on CHSTC-1101, each unescaped::

        use {code} for code      -> opens a code panel that SWALLOWS the
                                    entire rest of the document
        use {noformat} in prose  -> '<p>use </p>\\n<div class="preformatted
                                    panel"><pre></pre></div>\\n<p> in prose</p>'
                                    the paragraph torn into three
        use {panel} in prose     -> an empty '<div class="panel">' inside <p>
        use {quote} in prose     -> an empty '<blockquote>' inside <p>
        use {color:red} in prose -> an empty '<font color="red">'

    Escaped, every one renders as a single intact ``<p>`` with the literal
    text - measured, e.g. ``use \\{code} for code`` ->
    ``<p>use {code} for code</p>``.

    ``{{mono}}`` stays escaped for a different reason: it is monospace, not
    a macro, and the ``(?<!\\{)`` guard on the macro scanner deliberately
    refuses to count the ``{mono}`` token hiding in its tail - otherwise a
    monospace payload could pair with a real macro's closer.
    """
    assert preprocessor.markdown_to_jira(raw_wiki) == expected


def test_macro_exemption_is_per_offset_not_per_document(preprocessor):
    """ADVERSARIAL: exempting a macro must not exempt the defect-1 braces near it.

    The exemption is computed as a set of ``{`` OFFSETS, so a document that
    mixes both must escape the content braces and keep only the macro's.
    A document-level "this looks like wiki, skip escaping" guard - the other
    remedy considered - fails exactly here, and failing here re-opens
    defect 1 in the most likely real document there is: a description that
    contains both a code block and a brace-wrapped path parameter.
    """
    assert preprocessor.markdown_to_jira(
        "before a{b}c after {code}x{code} and {id} too"
    ) == (r"before a\{b}c after {code}x{code} and \{id} too")
    # Also with the macro FIRST, so an off-by-one in the offset set shows.
    assert preprocessor.markdown_to_jira("{color:red}r{color} then a{b}c") == (
        r"{color:red}r{color} then a\{b}c"
    )


@pytest.mark.parametrize(
    "raw_wiki, expected",
    [
        pytest.param(
            "{Code:java}x{CODE}",
            "{Code:java}x{CODE}",
            id="pairing-is-case-insensitive",
        ),
        pytest.param(
            "{color:red}a{color} and {color:blue}b{color}",
            "{color:red}a{color} and {color:blue}b{color}",
            id="two-complete-pairs",
        ),
        pytest.param(
            "a {table-plus}x{table-plus} b",
            r"a \{table-plus}x\{table-plus} b",
            id="hyphenated-uninstalled-name-still-escaped",
        ),
        pytest.param(
            "{code}x{code} and {panel}y{panel}",
            "{code}x{code} and {panel}y{panel}",
            id="two-different-paired-macros",
        ),
    ],
)
def test_macro_pairing_edge_cases(preprocessor, raw_wiki, expected):
    """Pairing is counted per NAME, case-insensitively, in document order."""
    assert preprocessor.markdown_to_jira(raw_wiki) == expected


def test_inline_code_content_never_gets_the_macro_exemption(preprocessor):
    """Content the author fenced as code is content, unambiguously.

    The exemption is passed at the DOCUMENT call site only. Inside
    ``{{monospace}}`` the author has already said "this is literal", so
    ```` `{code}x{code}` ```` must stay a monospace span holding literal
    text rather than become a real code panel nested inside one.
    """
    assert preprocessor.markdown_to_jira("`{code}x{code}`") == (r"{{\{code}x\{code}}}")
    assert preprocessor.markdown_to_jira("`{anchor:top}`") == r"{{\{anchor:top}}}"
    # ... and it still round-trips back to the author's Markdown.
    assert preprocessor.jira_to_markdown(r"{{\{anchor:top}}}") == "`{anchor:top}`"


@pytest.mark.parametrize(
    "stored, expected",
    [
        pytest.param(
            "user typed <sub>x</sub> and <sup>y</sup>",
            "user typed ~x~ and ^y^",
            id="sub-and-sup",
        ),
        pytest.param("a <cite>x</cite> b", "a ??x?? b", id="cite"),
        pytest.param("a <ins>x</ins> b", "a +x+ b", id="ins"),
    ],
)
def test_user_typed_interchange_tags_are_reinterpreted(preprocessor, stored, expected):
    """ACCEPTED SIDE EFFECT of using real HTML tags as the interchange format.

    ``jira_to_markdown`` emits ``<cite>``/``<ins>``/``<sup>``/``<sub>``/
    ``<span style=...>`` for wiki spans Markdown cannot express, and those
    tags are now protected from markdownify so the writer can map them back.
    The cost is that a user who TYPES one of them into a Jira field has it
    re-interpreted as markup on write-back: 'user typed <sub>x</sub>' now
    comes back as 'user typed ~x~', which renders as a real subscript.

    Before the fix markdownify DELETED the tags and kept only their text
    ('user typed x and y'), so both behaviours are lossy - but the new one
    changes the rendered appearance rather than just dropping tags, which is
    worse for someone documenting HTML in a ticket. Making the two
    distinguishable needs a private marker instead of real tags for the
    reader -> writer handoff, which is a change to the protected-tag list in
    ``preprocessing/base.py`` as well as here.
    """
    assert round_trip(preprocessor, stored) == expected


def test_multiline_monospace_write_back_gains_escapes(preprocessor):
    """RECORDED: the newline bound on ``{{...}}`` has a write-back consequence.

    The read-side trade-off is already documented (a genuinely multi-line
    span is left as raw wiki rather than converted). Its other half is that
    the writer then escapes that raw wiki, so the stored value drifts from
    '{{multi\\nline}} mono' to '\\{\\{multi\\nline}} mono' where it used to be
    byte-identical.

    MEASURED, so this is not a fear: both forms render identically as
    '<p>{{multi<br/>line}} mono</p>'. Nothing visible changes; only the
    stored bytes drift. Recorded so a reviewer re-deriving it does not
    conclude the escape broke something.
    """
    stored = "{{multi\nline}} mono"
    assert preprocessor.jira_to_markdown(stored) == stored
    assert round_trip(preprocessor, stored) == "\\{\\{multi\nline}} mono"


def test_nested_list_indent_still_doubles_each_cycle(preprocessor):
    """PRE-EXISTING and UNBOUNDED - pinned here with the real mechanism.

    Verification reported this as "the reader's indent width and the writer's
    'ident // 2' must agree". They DO agree: '** nested' is read at indent
    level 1 (two spaces) and written back as '**'. The actual cause is one
    line earlier - the read path's emphasis rule,
    ``'(?<!\\\\)([*_])(.*?)(?<!\\\\)\\1'``, matches the EMPTY span in the '**'
    bullet marker and rewrites it to '****'. Only then does the list rule
    see a level-3 bullet and emit six spaces.

    Reproduces identically on the pre-fix code, so it is not caused by
    PROPX-398 and is deliberately not repaired inside it - requiring
    non-empty emphasis content is a change to a regex that four other
    contracts depend on. Pinned so the follow-up ticket starts from the
    measured mechanism instead of re-deriving it, and so the day someone
    fixes it this test tells them the blast radius.
    """
    stored = "* one\n* two\n** nested"
    first = round_trip(preprocessor, stored)
    second = round_trip(preprocessor, first)
    assert first == "* one\n* two\n**** nested"
    assert second == "* one\n* two\n******** nested"
    # The mechanism, isolated: an empty emphasis span in the bullet marker.
    assert preprocessor.jira_to_markdown("** nested") == "      - nested"
