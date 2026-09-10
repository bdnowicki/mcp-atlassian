"""Regression tests for PROPX-398 - read-path corruption.

Defect 2: ``jira_to_markdown`` corrupted content on nearly every read, and a
second markdownify stage inside ``_convert_html_to_markdown`` then escaped or
deleted what the first stage had correctly produced.

Measured on Jira Server/DC (CHSTC-1101/1102, rendered HTML fetched over raw
REST, 2026-09-10):

* 2a - four unanchored inline-span regexes let every delimiter open the next
  span, so ``1+2+3 and a+b and C++ language and A+B+C`` came back as
  interleaved ``<ins>`` runs and, end to end, as ``123 and ab and C language``
  with every ``+`` gone. Jira itself renders that string character for
  character, so the converter must not touch it.
* 2b - the monospace pattern ``\\{\\{([^}]+)\\}\\}`` could not cross an inner
  brace, so ``{{GET /rest/api/2/issue/{issueKey}}}`` read back with a stray
  brace outside the closing backtick, and a wiki-escaped brace made it not
  match at all, leaking raw wiki into a field documented as Markdown.
* 2c - ``\\[(.+?)\\]([^\\(])`` stripped ANY bracketed text, so a bracketed
  issue key lost the brackets that make Jira render it as a link.
* 2d - markdownify backslash-escaped the ``**``/``_`` this module had already
  produced and silently DELETED the ``<cite>``/``<ins>``/``<sup>``/``<sub>``/
  ``<span style=...>`` interchange tags it emits for wiki spans Markdown
  cannot express.

Also pinned here: the backslash guards on every macro opener AND closer. Once
the writer escapes ``{``, a read path that parses ``\\{color:red}`` as a macro
permanently deletes the literal text the escape existed to protect.
"""

import time

import pytest

from mcp_atlassian.preprocessing.jira import JiraPreprocessor


@pytest.fixture
def preprocessor() -> JiraPreprocessor:
    """A default Server/DC preprocessor - markup translation enabled."""
    return JiraPreprocessor()


# --------------------------------------------------------------------------
# Defect 2a - the inline-span family
# --------------------------------------------------------------------------


def test_plus_runs_not_interleaved(preprocessor: JiraPreprocessor) -> None:
    """LIVE: Jira renders plus runs character for character; so must the reader."""
    # Before the fix: '1<ins>2</ins>3 and a<ins>b and C</ins><ins> language and
    # A</ins>B+C', and end to end through clean_jira_text: '123 and ab and C
    # language and AB+C' - every '+' destroyed.
    text = "1+2+3 and a+b and C++ language and A+B+C"
    assert preprocessor.jira_to_markdown(text) == text


def test_word_boundary_plus_still_inserts(preprocessor: JiraPreprocessor) -> None:
    """LIVE: Jira renders '+real underline+' as <ins>, and so does the reader."""
    result = preprocessor.jira_to_markdown("a +real underline+ here")
    assert "<ins>real underline</ins>" in result


def test_double_plus_stays_literal_accepted_conservatism(
    preprocessor: JiraPreprocessor,
) -> None:
    """Doubled delimiters stay literal - a DELIBERATE false negative.

    Jira itself renders '++inserted++' as '+<ins>inserted</ins>+'. The guarded
    pattern refuses a doubled delimiter and leaves the whole thing literal.
    Neither lookaround can be relaxed to chase this without also unguarding
    'C++' and '1+2+3', which is real corruption rather than a missing
    conversion. Recorded as a test so it stays a decision, not a surprise:
    exact fidelity for doubled delimiters needs a separate narrower rule and
    its own live matrix - do NOT achieve it by loosening the span guard.
    """
    assert preprocessor.jira_to_markdown("++inserted++") == "++inserted++"


@pytest.mark.parametrize(
    "wiki",
    [
        pytest.param("a^2 and b^3 and 2^n", id="exponent-shorthand"),
        pytest.param("2^10 and x^y^z carets", id="caret-runs"),
    ],
)
def test_caret_runs_not_superscripted(
    preprocessor: JiraPreprocessor, wiki: str
) -> None:
    """Intraword carets are literal; line 316 carried the identical 2a defect."""
    # Before the fix: 'a<sup>2 and b</sup>3 and 2^n'.
    assert preprocessor.jira_to_markdown(wiki) == wiki


def test_word_boundary_caret_still_superscripts(preprocessor: JiraPreprocessor) -> None:
    """LIVE: Jira renders 'x ^2^ end' as a <sup> span."""
    assert "<sup>2</sup>" in preprocessor.jira_to_markdown("x ^2^ end")


@pytest.mark.parametrize(
    "wiki",
    [
        pytest.param("~/.mcp-atlassian.env and ~/tmp", id="home-paths"),
        pytest.param("path ~/tmp and a ~ b", id="path-and-loose-tilde"),
        pytest.param("H~2~O", id="intraword-tilde-refused"),
    ],
)
def test_tilde_paths_not_subscripted(preprocessor: JiraPreprocessor, wiki: str) -> None:
    """Home-relative paths must survive; the old rule ate them as subscripts."""
    # Before the fix: '<sub>/.mcp-atlassian.env and </sub>/tmp'.
    assert preprocessor.jira_to_markdown(wiki) == wiki


def test_word_boundary_tilde_still_subscripts(preprocessor: JiraPreprocessor) -> None:
    """LIVE: Jira renders 'H ~2~ O' as a <sub> span."""
    assert "<sub>2</sub>" in preprocessor.jira_to_markdown("H ~2~ O")


def test_strikethrough_now_read_and_hyphens_safe(
    preprocessor: JiraPreprocessor,
) -> None:
    """Jira strikethrough is now read at all - line 322 was a literal no-op.

    ``re.sub(r"-([^-]*)-", r"-\\1-", output)`` replaced a dash span with
    itself, so ``-deleted-`` was never converted. Repairing it with an
    UNGUARDED pattern is the trap: that turns 'well-known-name' into
    'well<del>known</del>name'. The six negatives below are the widest blast
    radius in this change.
    """
    assert "<del>deleted text</del>" in preprocessor.jira_to_markdown(
        "a -deleted text- b"
    )


@pytest.mark.parametrize(
    "wiki",
    [
        pytest.param("well-known and pre-fix and 2024-01-01", id="hyphenated-words"),
        pytest.param("text\n----\nmore", id="horizontal-rule"),
        pytest.param("- one\n- two", id="bullet-lines"),
        pytest.param("-5 degrees at line start", id="leading-negative-number"),
        pytest.param("5 - 3 - 1 minus runs", id="minus-runs"),
        pytest.param("--flag", id="cli-flag"),
    ],
)
def test_strikethrough_negative_controls(
    preprocessor: JiraPreprocessor, wiki: str
) -> None:
    """Hyphens that are not a strikethrough span must be returned untouched."""
    assert preprocessor.jira_to_markdown(wiki) == wiki


def test_citation_guarded(preprocessor: JiraPreprocessor) -> None:
    """'??' converts at word boundaries only; stray pairs stay literal.

    'what?? really??' became 'what<cite> really</cite>' before the fix. The
    non-overlapping alternation is kept as-is - it is the ReDoS fix that
    test_jira_to_markdown_citation_no_redos pins - and only boundary
    lookarounds were added.
    """
    assert "<cite>cite</cite>" in preprocessor.jira_to_markdown("a ??cite?? b")
    assert preprocessor.jira_to_markdown("a??cite??b") == "a??cite??b"
    assert preprocessor.jira_to_markdown("what?? really??") == "what?? really??"


# --------------------------------------------------------------------------
# Defect 2b - monospace
# --------------------------------------------------------------------------


def test_monospace_with_inner_brace(preprocessor: JiraPreprocessor) -> None:
    """A stray trailing brace now reads back INSIDE the backticks.

    The STORED input here is itself already-broken markup - written by the
    pre-fix converter, it renders as a torn paragraph with a macro block. This
    test therefore pins graceful READ-BACK, not renderability; never assert a
    clean <tt> for this shape. Before the fix:
    '`GET /rest/api/2/issue/{issueKey`}' - the closing backtick misplaced.
    """
    assert (
        preprocessor.jira_to_markdown("{{GET /rest/api/2/issue/{issueKey}}}")
        == "`GET /rest/api/2/issue/{issueKey}`"
    )


def test_monospace_with_wiki_escape_unescaped(preprocessor: JiraPreprocessor) -> None:
    """Monospace holding a wiki-escaped brace reads back as clean Markdown.

    This closes the loop with the write path, which now emits exactly this
    shape. Before the fix the ``[^}]+`` class did not match AT ALL, so
    clean_jira_text returned the raw wiki unchanged and leaked
    '{{GET /x/\\\\{k\\\\} tail}}' into a field documented as Markdown.
    """
    assert preprocessor.jira_to_markdown(r"{{GET /x/\{k} tail}}") == (
        "`GET /x/{k} tail`"
    )


@pytest.mark.parametrize(
    "wiki, expected",
    [
        pytest.param(
            "{{<Tag>}} and {{<Elem>}}",
            "`<Tag>` and `<Elem>`",
            id="angle-bracket-spans",
        ),
        pytest.param("{{a}} and {{b}}", "`a` and `b`", id="short-spans"),
    ],
)
def test_adjacent_monospace_spans_not_merged(
    preprocessor: JiraPreprocessor, wiki: str, expected: str
) -> None:
    """STANDING GUARD: the quantifier must stay LAZY.

    A greedy, last-'}}'-anchored variant merges adjacent spans -
    '{{<Tag>}} and {{<Elem>}}' becomes '`<Tag>}} and {{<Elem>`'. Do not
    "simplify" the pattern by dropping the '?'.
    """
    assert preprocessor.jira_to_markdown(wiki) == expected


def test_multiline_monospace_left_raw(preprocessor: JiraPreprocessor) -> None:
    """ACCEPTED TRADE-OFF of the newline bound, recorded as a decision.

    The old negated class ``[^}]+`` matched newlines, so a multi-line
    ``{{...}}`` span was converted. The new pattern is newline-bounded (that
    bound is what stops one span's opener pairing with a later line's
    terminator), so a genuinely multi-line span is now left as raw wiki
    instead of being converted.
    """
    assert preprocessor.jira_to_markdown("{{multi\nline}}") == "{{multi\nline}}"


# --------------------------------------------------------------------------
# Brace escapes: the blocking prerequisite for the write-path escape
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wiki, expected",
    [
        pytest.param(r"a \{k} b", "a {k} b", id="opening-brace-only"),
        pytest.param(r"a \{k\} b", "a {k} b", id="both-braces"),
    ],
)
def test_prose_brace_escape_stripped(
    preprocessor: JiraPreprocessor, wiki: str, expected: str
) -> None:
    """Markdown has no brace escape, so a faithful reader drops it."""
    # Before the fix the backslashes came back intact, surfacing literal
    # backslash noise into a field whose contract says Markdown.
    assert preprocessor.jira_to_markdown(wiki) == expected


@pytest.mark.parametrize(
    "wiki, expected",
    [
        pytest.param(
            r"literal \{color:red}x\{color} typed",
            "literal {color:red}x{color} typed",
            id="colour",
        ),
        pytest.param(
            r"literal \{code}c\{code} typed",
            "literal {code}c{code} typed",
            id="code",
        ),
        pytest.param(
            r"literal \{noformat}n\{noformat} typed",
            "literal {noformat}n{noformat} typed",
            id="noformat",
        ),
        pytest.param(
            r"literal \{quote}q\{quote} typed",
            "literal {quote}q{quote} typed",
            id="quote",
        ),
        pytest.param(
            r"literal \{panel}b\{panel} typed",
            "literal {panel}b{panel} typed",
            id="panel",
        ),
        pytest.param(
            r"literal \{{mono}} typed",
            "literal {{mono}} typed",
            id="monospace",
        ),
        pytest.param(
            r"literal \{\{mono}} typed",
            "literal {{mono}} typed",
            id="monospace-as-the-writer-emits-it",
        ),
    ],
)
def test_escaped_macro_literal_survives(
    preprocessor: JiraPreprocessor, wiki: str, expected: str
) -> None:
    """A backslash-escaped macro is literal text and must NOT be parsed.

    THE BLOCKING PREREQUISITE for the write-path brace escape. Before the
    guards the read path consumed the delimiters and PERMANENTLY DELETED the
    literal text: '\\{color:red}x\\{color}' became
    '\\<span style=...>x\\</span>', '\\{quote}q\\{quote}' became '\\> q\\',
    '\\{panel}b\\{panel}' became '\\\\nb\\\\n' and '\\{code}c\\{code}' became a
    fence. That was inert only because nothing emitted '\\{macro}' - the
    moment the writer escape ships it is live data loss, which is why the
    writer escape and these guards must land together.
    """
    assert preprocessor.jira_to_markdown(wiki) == expected


@pytest.mark.parametrize(
    "wiki, expected_substr",
    [
        pytest.param(
            "{color:#ff0000}red{color}",
            '<span style="color:#ff0000">',
            id="colour-macro",
        ),
        pytest.param("{code:python}x=1{code}", "```python", id="code-macro"),
        pytest.param("{noformat}n{noformat}", "```", id="noformat-macro"),
        pytest.param("{quote}q{quote}", "> q", id="quote-macro"),
        pytest.param("{panel:title=T}body{panel}", "**T**", id="panel-macro"),
        pytest.param("{{mono}}", "`mono`", id="monospace-macro"),
    ],
)
def test_genuine_macros_still_convert(
    preprocessor: JiraPreprocessor, wiki: str, expected_substr: str
) -> None:
    """NEGATIVE CONTROL: the backslash guards did not disable the macros."""
    assert expected_substr in preprocessor.jira_to_markdown(wiki)


# --------------------------------------------------------------------------
# Defect 2c - bracket stripping
# --------------------------------------------------------------------------


def test_issue_key_brackets_preserved(preprocessor: JiraPreprocessor) -> None:
    """LIVE: '[CHSTC-1101]' renders as a proper issue <a href> - keep the brackets.

    Before the fix the brackets were stripped, which destroys real markup and
    breaks the link when the text is written back.
    """
    text = "See [CHSTC-1101] for details"
    assert preprocessor.jira_to_markdown(text) == text


@pytest.mark.parametrize(
    "wiki",
    [
        pytest.param("array[0] index", id="index-expression"),
        pytest.param("list[i][j]", id="nested-index"),
    ],
)
def test_code_like_brackets_preserved(
    preprocessor: JiraPreprocessor, wiki: str
) -> None:
    """Subscript expressions in prose kept their brackets ('array0 index' before)."""
    assert preprocessor.jira_to_markdown(wiki) == wiki


@pytest.mark.parametrize(
    "wiki",
    [
        pytest.param("ask [~jsmith] now", id="user-mention"),
        pytest.param("jump [#top] now", id="anchor"),
        pytest.param("file [^notes.txt] now", id="attachment"),
        pytest.param("status [WIP] and note [1]", id="plain-bracketed-text"),
    ],
)
def test_user_anchor_attachment_links_preserved(
    preprocessor: JiraPreprocessor, wiki: str
) -> None:
    """LIVE: stripping these brackets destroys the target or rewrites the author.

    '[~jsmith]' is a real profile link and '[#top]' a real anchor, so removing
    the brackets destroys the link target. '[^notes.txt]' and '[WIP]' render as
    span.error when bracketed, so stripping silently rewrites what the author
    wrote. This is the case that rejects any broader bracket stripper: only a
    ``scheme:`` prefix is safe to unwrap.
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
    ],
)
def test_scheme_urls_still_stripped(
    preprocessor: JiraPreprocessor, wiki: str, expected: str
) -> None:
    """LIVE: bracketed and bare URLs render the identical link, so unwrap is safe."""
    assert preprocessor.jira_to_markdown(wiki) == expected


@pytest.mark.parametrize(
    "wiki, expected",
    [
        pytest.param(
            "trailing [https://x.example/z]",
            "trailing https://x.example/z",
            id="end-of-string",
        ),
        pytest.param(
            "mid [https://x.example/z] here",
            "mid https://x.example/z here",
            id="mid-string",
        ),
    ],
)
def test_eos_and_midstring_behave_identically(
    preprocessor: JiraPreprocessor, wiki: str, expected: str
) -> None:
    """Pins the non-consuming lookahead that replaced ``([^\\(])``.

    The old trailing group both REQUIRED and CONSUMED a character, so '[X]' at
    end-of-string was never stripped while '[X]\\n' ate the newline.
    """
    assert preprocessor.jira_to_markdown(wiki) == expected


@pytest.mark.parametrize(
    "wiki",
    [
        pytest.param("md [t](https://x/y) keep", id="markdown-link"),
        pytest.param("img ![alt](https://x/y.png)", id="markdown-image"),
    ],
)
def test_markdown_link_and_image_not_touched(
    preprocessor: JiraPreprocessor, wiki: str
) -> None:
    """NEGATIVE CONTROL for the ``(?!\\()`` lookahead and the ``[!\\w\\]]`` lookbehind.

    The wiki-link rule runs immediately before the bare-link rule, so the
    bare-link rule must not attack the '[text](url)' links the previous line
    just produced. That order is load-bearing.
    """
    assert preprocessor.jira_to_markdown(wiki) == wiki


def test_wiki_link_still_converts_to_markdown(preprocessor: JiraPreprocessor) -> None:
    """The '[label|url]' -> '[label](url)' conversion is unaffected."""
    assert (
        preprocessor.jira_to_markdown("see [our website|https://example.com] now")
        == "see [our website](https://example.com) now"
    )


# --------------------------------------------------------------------------
# Defect 2d - the stacked markdownify stage
# --------------------------------------------------------------------------


def test_bold_survives_the_markdownify_stage(preprocessor: JiraPreprocessor) -> None:
    """End to end: emphasis is not backslash-escaped and wiki tags are not deleted.

    The single highest-value assertion in this change. It pins BOTH halves of
    the base.py fix: markdownify's escape_asterisks/escape_underscores are off,
    and the <cite>/<ins>/<sup>/<sub>/<span> interchange tags are protected from
    markdownify, which drops unknown tags and keeps only their text. Before the
    fix: '\\*\\*BOLD\\*\\* and \\*ital\\* and cite and ins and `code`'.
    """
    assert preprocessor.clean_jira_text(
        "*BOLD* and _ital_ and ??cite?? and +ins+ and {{code}}"
    ) == ("**BOLD** and *ital* and <cite>cite</cite> and <ins>ins</ins> and `code`")


def test_bold_alone_is_not_a_valid_regression_test(
    preprocessor: JiraPreprocessor,
) -> None:
    """DOCUMENTS THE TRAP: this assertion ALREADY PASSED against the bug.

    The markdownify stage is conditional on a ``re.search(r"<[^>]+>", text)``
    guard, so a document with no tags at all never reaches markdownify and its
    bold is never escaped. Anyone writing a defect-2d regression test with bold
    alone will believe it covers the fix. Use a co-occurring tag producer -
    '??cite??' or a '{color:...}' macro - and NOT '+ins+', whose fix changes
    when a tag is emitted at all.
    """
    assert preprocessor.clean_jira_text("*BOLD* text") == "**BOLD** text"


def test_snake_case_survives_the_markdownify_stage(
    preprocessor: JiraPreprocessor,
) -> None:
    """Identifiers keep their underscores ('snake\\\\_case' before the fix)."""
    result = preprocessor.clean_jira_text("snake_case and ??cite??")
    assert "snake_case" in result
    assert "snake\\_case" not in result


def test_emphasis_not_escaped_when_real_html_is_present(
    preprocessor: JiraPreprocessor,
) -> None:
    """Exercises the markdownify call itself, which the tag guard otherwise skips.

    A document whose only tags are protected wiki tags no longer trips the
    ``<[^>]+>`` guard, so markdownify is skipped entirely for it - correct, but
    it means the escape flags are only exercised by a document that also
    carries REAL HTML. This is that document. Before the fix:
    '\\*\\*BOLD\\*\\* with **html bold** and snake\\_case'.
    """
    assert preprocessor.clean_jira_text(
        "*BOLD* with <b>html bold</b> and snake_case"
    ) == ("**BOLD** with **html bold** and snake_case")


def test_del_is_deliberately_not_protected(preprocessor: JiraPreprocessor) -> None:
    """<del> stays unprotected: markdownify's '~~x~~' round-trips via the writer.

    markdownify renders <del> as ``~~x~~``, which the write path's own
    ``~~(.*?)~~`` rule converts back to a Jira dash span, so protecting it
    would gain nothing and would keep raw HTML in a Markdown field.
    """
    assert preprocessor.clean_jira_text("-gone- and <b>b</b>") == ("~~gone~~ and **b**")


# --------------------------------------------------------------------------
# The interchange tags: the read path emits them, the writer maps them back
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wiki, markdown",
    [
        pytest.param("a ??cite?? b", "a <cite>cite</cite> b", id="cite"),
        pytest.param("a +ins+ b", "a <ins>ins</ins> b", id="ins"),
        pytest.param("x ^2^ end", "x <sup>2</sup> end", id="sup"),
        pytest.param("H ~2~ O", "H <sub>2</sub> O", id="sub"),
        pytest.param("a -deleted text- b", "a <del>deleted text</del> b", id="del"),
    ],
)
def test_wiki_span_round_trips_both_directions(
    preprocessor: JiraPreprocessor, wiki: str, markdown: str
) -> None:
    r"""Pins the writer's tag_map, which defect 2d had left as dead code.

    Markdown cannot express Jira's citation, insert, superscript, subscript
    and strikethrough spans, so ``jira_to_markdown`` emits HTML tags as an
    interchange format and ``markdown_to_jira`` maps them back. Before the
    2d fix markdownify DELETED <cite>/<ins>/<sup>/<sub> on the way out, so no
    caller ever saw one and the writer's tag_map was unreachable in practice.
    Every read can now emit them, which makes that mapping load-bearing in
    both directions.

    The failure this guards is silent: the writer ends with a generic
    ``<([^>]+)>`` -> ``[\1]`` fallback, so a tag_map entry dropped or
    renamed does not raise - it quietly degrades a read-then-write into
    '[cite]x[/cite]'. Measured with a tag the map does not contain:
    ``markdown_to_jira('<cyte>x</cyte>')`` returns '[cyte]x[/cyte]'.

    Each case is asserted in BOTH directions, so the pair also documents the
    word-boundary guard: the spans only convert where Jira converts them.
    """
    assert preprocessor.jira_to_markdown(wiki) == markdown
    assert preprocessor.markdown_to_jira(markdown) == wiki


def test_del_round_trips_via_the_markdownify_route(
    preprocessor: JiraPreprocessor,
) -> None:
    """<del> has a SECOND live route and both must land on the same wiki.

    ``jira_to_markdown`` emits '<del>', but ``clean_jira_text``'s markdownify
    stage rewrites it to '~~x~~' because <del> is deliberately left out of the
    protected-tag list. The writer therefore has to bring BOTH shapes back to
    '-x-': the tag_map entry serves callers that use ``jira_to_markdown``
    directly, and the ``~~(.*?)~~`` rule serves the full read pipeline.
    Removing either one breaks exactly one route, and only one of the two
    assertions below would notice.
    """
    assert preprocessor.clean_jira_text("a -deleted text- b") == (
        "a ~~deleted text~~ b"
    )
    assert preprocessor.markdown_to_jira("a ~~deleted text~~ b") == (
        "a -deleted text- b"
    )


@pytest.mark.parametrize(
    "wiki, expected",
    [
        pytest.param(
            "a ??cite?? and <b>b</b>",
            "a <cite>cite</cite> and **b**",
            id="cite",
        ),
        pytest.param(
            "a +ins+ and <b>b</b>",
            "a <ins>ins</ins> and **b**",
            id="ins",
        ),
        pytest.param(
            "x ^2^ and <b>b</b>",
            "x <sup>2</sup> and **b**",
            id="sup",
        ),
        pytest.param(
            "H ~2~ and <b>b</b>",
            "H <sub>2</sub> and **b**",
            id="sub",
        ),
        pytest.param(
            "{color:red}r{color} and <b>b</b>",
            '<span style="color:red">r</span> and **b**',
            id="span",
        ),
    ],
)
def test_interchange_tags_survive_markdownify_end_to_end(
    preprocessor: JiraPreprocessor, wiki: str, expected: str
) -> None:
    """Pins the protected-tag list, which a read-direction test cannot reach.

    ``jira_to_markdown`` never runs markdownify, so the bidirectional tests
    above stay green even if the protection is deleted. These documents each
    carry a co-occurring REAL '<b>' tag, which is what makes
    ``_convert_html_to_markdown``'s ``re.search(r"<[^>]+>", text)`` guard fire
    after the wiki tags have been swapped out for placeholders - a document
    whose only tags are protected ones skips markdownify altogether and would
    pass trivially.

    Measured on markdownify itself: <cite>, <ins>, <sup>, <sub> and
    <span style=...> all reduce to their bare text ('<sup>2</sup>' -> '2'), so
    an unprotected tag means the span is DELETED, not merely reshaped - the
    colour information or the exponent is simply gone. <del> is the one tag
    left unprotected on purpose; see the test above for why that is safe.
    """
    assert preprocessor.clean_jira_text(wiki) == expected


# --------------------------------------------------------------------------
# Colour spans (visible only once the interchange tags are protected)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wiki, markdown",
    [
        pytest.param(
            "{color:#ff0000}red{color} text",
            '<span style="color:#ff0000">red</span> text',
            id="hex-colour",
        ),
        pytest.param(
            "{color:red}Red{color}",
            '<span style="color:red">Red</span>',
            id="named-colour",
        ),
    ],
)
def test_colour_span_round_trips_both_forms(
    preprocessor: JiraPreprocessor, wiki: str, markdown: str
) -> None:
    """Both directions were broken; assert with REAL quotes, not backslash-quotes.

    The read side's replacement was a raw string containing ``\\"``, and re.sub
    leaves unknown escapes alone in a TEMPLATE, so it emitted literal
    backslashes ('<span style=\\\\"color:#ff0000\\\\">'). markdownify then
    silently deleted the span, so the colour was simply lost; with the tags
    protected, the same junk would have become visible instead. Behaviour
    change to name in the PR: colour information the old read path discarded
    now survives, so it will reappear in stored fields.
    """
    assert preprocessor.jira_to_markdown(wiki) == markdown
    assert preprocessor.markdown_to_jira(markdown) == wiki


# --------------------------------------------------------------------------
# Perf / ReDoS guards for the new patterns
# --------------------------------------------------------------------------


def test_redos_and_perf_guards(preprocessor: JiraPreprocessor) -> None:
    """NEGATIVE CONTROL: the new patterns add no measurable cost.

    Re-runs the dense unmatched-'??' plus dense-'{{...}}' document that
    test_jira_to_markdown_citation_no_redos pins, 20 times over. The document
    is converted 20 times rather than concatenated 20 times: concatenating it
    lets one repetition's stray '(??)' pair with the next one's, which is a
    property of the synthetic input (true before this change too), not of the
    new patterns.
    """
    description = (
        "h2. Known limitations\n"
        "* (??) The {{retry-handler}} -> {{fallback}} path is *broken* "
        "if the upstream timeout during {{retry-handler}} has not "
        "elapsed yet. Each component would need to track pending "
        "requests and report a metric. _This means a request could "
        "be stuck in {{retry-handler}} indefinitely._\n"
        "* Each component must validate the configuration and *stop* "
        "after detecting an invalid setting.\n"
        "h2. Monitoring\n"
        "* Report the current status through a *metric*."
    )

    start = time.perf_counter()
    for _ in range(20):
        result = preprocessor.jira_to_markdown(description)
    elapsed = time.perf_counter() - start

    assert "<cite>" not in result
    assert "`retry-handler`" in result
    assert elapsed < 1.0, f"took {elapsed:.3f}s"


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("inline `GET /x/{k}` and bare {p} here", id="braces-mixed"),
        pytest.param("see `/issue/{id}/end` now", id="brace-midspan"),
        pytest.param("the foo_bar identifier", id="intraword-underscore"),
        pytest.param("**BOLD** and _ital_", id="emphasis"),
        pytest.param("1+2+3 and a+b and C++ language and A+B+C", id="plus-runs"),
        pytest.param("~/.mcp-atlassian.env and a^2 here", id="tilde-and-caret"),
        pytest.param("## Section {A}", id="heading-brace"),
        pytest.param(
            "literal {color:red}x{color} typed by user", id="user-typed-macro"
        ),
        # The five interchange spans. Verified fixpoints, and the only
        # coverage that walks a cite/ins/sup/sub/del span through the WHOLE
        # write -> read -> write pipeline rather than one direction at a time.
        pytest.param("a <cite>cite</cite> b", id="cite-span"),
        pytest.param("a <ins>ins</ins> b", id="ins-span"),
        pytest.param("x <sup>2</sup> end", id="sup-span"),
        pytest.param("H <sub>2</sub> O", id="sub-span"),
        pytest.param("a ~~deleted text~~ b", id="del-span"),
    ],
)
def test_round_trip_fixpoint(preprocessor: JiraPreprocessor, source: str) -> None:
    """write -> read -> write must be a fixpoint, so writes may derive from reads.

    The executable form of the ticket's central claim, and the only test that
    would have caught the '\\\\{' double-escape catastrophe. It fails if the
    write-path escape lands without the read-path macro guards and terminal
    unescape. Before the fix at least the brace, bold and plus cases drifted:
    one cycle lost every '+' and the cite/ins markers, and '*BOLD*' became
    '\\*\\*BOLD\\*\\*' and then '\\_\\_BOLD\\_\\_' - the characters themselves
    mutated.

    Note this is the REAL pipeline (markdown_to_jira of clean_jira_text
    output). ``markdown_to_jira`` applied twice to its OWN output was already
    non-idempotent before this change and is documented markdown -> wiki only.
    """
    first_write = preprocessor.markdown_to_jira(source)
    read_back = preprocessor.clean_jira_text(first_write)
    second_write = preprocessor.markdown_to_jira(read_back)
    assert second_write == first_write
