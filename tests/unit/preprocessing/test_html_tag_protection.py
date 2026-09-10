"""Regression tests for PROPX-398 - which HTML tags the read path protects.

``_convert_html_to_markdown`` (preprocessing/base.py) runs a markdownify stage
over the output of ``jira_to_markdown``. markdownify unwraps unknown tags to
their bare text, which silently DELETED the ``<cite>``/``<ins>``/``<sup>``/
``<sub>``/``<span style="color:...">`` interchange tags ``jira_to_markdown``
emits for the wiki spans Markdown cannot express (defect 2d). Those tags are
now protected from that stage with placeholders.

This file pins the BOUNDARY of that protection, which is load bearing in both
directions:

* Protected: exactly the shapes ``markdown_to_jira`` can map back - a bare
  ``<cite>``/``<ins>``/``<sup>``/``<sub>`` PAIR, and a
  ``<span style="color:...">`` pair. ``<del>`` is deliberately excluded;
  markdownify renders it as ``~~x~~`` and the writer's own ``~~(.*?)~~`` rule
  converts that back to a Jira dash span.
* NOT protected: foreign HTML that happens to use one of those tag names -
  ``<span class="x">``, ``<ins class="q">``, a bare ``<span>``, an unpaired
  ``</ins>``. The writer has no rule for those, so its generic
  ``<([^>]+)>`` -> ``[\\1]`` fallback turns any survivor into
  ``[span class="x"]``, which Jira renders as span.error bracket text. An
  over-broad protection pattern therefore MANUFACTURES corruption on a shape
  that was clean before: measured on the first cut of this fix,
  ``clean_jira_text('a <span class="x">y</span> b')`` returned its input
  verbatim (pre-fix: ``'a y b'``) and ``markdown_to_jira`` of that produced
  ``'a [span class="x"]y[/span] b'``.

Only the tag DELIMITERS are placeholdered, never the content, so real HTML
inside a protected span is still converted.
"""

import pytest

from mcp_atlassian.preprocessing.jira import JiraPreprocessor


@pytest.fixture
def preprocessor():
    return JiraPreprocessor()


# --------------------------------------------------------------------------
# The interchange tags stay protected
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wiki", "expected"),
    [
        pytest.param("a ??cite?? b", "a <cite>cite</cite> b", id="cite"),
        pytest.param("a +ins+ b", "a <ins>ins</ins> b", id="ins"),
        pytest.param("x ^2^ end", "x <sup>2</sup> end", id="sup"),
        pytest.param("H ~2~ O", "H <sub>2</sub> O", id="sub"),
        pytest.param(
            "{color:red}x{color} typed",
            '<span style="color:red">x</span> typed',
            id="colour-named",
        ),
        pytest.param(
            "{color:#ff0000}red{color} text",
            '<span style="color:#ff0000">red</span> text',
            id="colour-hex",
        ),
    ],
)
def test_interchange_tags_survive_the_markdownify_stage(preprocessor, wiki, expected):
    """The tags the writer maps back must reach the caller intact.

    Before the fix markdownify deleted all of them, so the wiki span was lost
    on every read and could never be written back.
    """
    assert preprocessor.clean_jira_text(wiki) == expected


def test_del_is_deliberately_not_protected(preprocessor):
    """``<del>`` is excluded on purpose - markdownify round-trips it itself.

    markdownify converts ``<del>x</del>`` to ``~~x~~``, which the writer's own
    ``~~(.*?)~~`` rule turns back into a Jira dash span, so protecting the tag
    would only add a second representation of the same thing.
    """
    assert preprocessor.clean_jira_text("a -deleted text- b") == (
        "a ~~deleted text~~ b"
    )
    assert preprocessor.markdown_to_jira("a ~~deleted text~~ b") == (
        "a -deleted text- b"
    )


# --------------------------------------------------------------------------
# Foreign HTML keeps its pre-fix handling
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        pytest.param('a <span class="x">y</span> b', "a y b", id="span-class"),
        pytest.param("a <span>plain</span> b", "a plain b", id="span-bare"),
        pytest.param(
            'a <span  style="color:red">x</span> b',
            "a x b",
            id="span-style-not-writer-shape",
        ),
        pytest.param('a <ins class="q">x</ins> b', "a x b", id="ins-with-attribute"),
        pytest.param('a <sup id="f">2</sup> b', "a 2 b", id="sup-with-attribute"),
        pytest.param("a </ins> stray b", "a stray b", id="unpaired-closing-tag"),
        pytest.param("a <cite>unclosed b", "a unclosed b", id="unpaired-opening-tag"),
    ],
)
def test_foreign_html_is_still_unwrapped_to_text(preprocessor, stored, expected):
    """Foreign HTML must be unwrapped exactly as it was before defect 2d's fix.

    Verified directly against markdownify: each of these inputs unwraps to the
    expected bare text, which is what the read path produced before the
    interchange tags were protected. Keeping that behaviour is what stops the
    writer's ``<([^>]+)>`` -> ``[\\1]`` fallback from emitting span.error
    bracket text - see the module docstring for the measured failure.
    """
    assert preprocessor.clean_jira_text(stored) == expected


def test_attributed_tag_does_not_leak_a_half_protected_pair(preprocessor):
    """An attributed opening tag must not leave its bare closing tag behind.

    Protecting bare tags INDIVIDUALLY rather than as a pair produced
    ``'a x</ins> b'`` here, because ``<ins class="q">`` was unwrapped while the
    bare ``</ins>`` was protected. Both delimiters are now required in one
    match, so an unpaired or attributed tag is never protected.
    """
    result = preprocessor.clean_jira_text('a <ins class="q">x</ins> b')

    assert result == "a x b"
    assert "</ins>" not in result
    assert preprocessor.markdown_to_jira(result) == "a x b"


def test_protected_and_foreign_tags_coexist(preprocessor):
    """A real interchange pair survives while a foreign one is unwrapped."""
    assert preprocessor.clean_jira_text(
        'a <ins>x</ins> and <ins class="q">y</ins> b'
    ) == ("a <ins>x</ins> and y b")


def test_protected_span_content_is_still_converted(preprocessor):
    """Only the delimiters are protected, so inner HTML still converts.

    Extracting the whole span as one placeholder would leave ``<b>bold</b>``
    literal inside the colour span; only the two delimiters are placeholdered.
    """
    assert preprocessor.clean_jira_text("{color:red}a <b>bold</b> b{color}") == (
        '<span style="color:red">a **bold** b</span>'
    )


# --------------------------------------------------------------------------
# Fixpoint (the R24 matrix has no span/attribute case)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param('a <span class="x">y</span> b', id="span-class"),
        pytest.param('a <ins class="q">x</ins> b', id="ins-with-attribute"),
        pytest.param("a <span>plain</span> b", id="span-bare"),
        pytest.param("{color:red}x{color} typed", id="colour-named"),
        pytest.param("{color:#ff0000}red{color} text", id="colour-hex"),
        pytest.param('{color:red}a{color} and <span class="x">b</span>', id="mixed"),
        pytest.param("a ??cite?? and ^2^ and ~n~ b", id="interchange-tags"),
    ],
)
def test_read_write_is_a_fixpoint_for_span_shapes(preprocessor, stored):
    """read -> write -> read -> write must not drift for any span shape.

    read -> write was already a fixpoint for foreign span HTML BEFORE the
    interchange tags were protected (markdownify unwrapped it and plain text
    writes back unchanged). An over-broad protection pattern broke that: the
    surviving ``<span class="x">`` became ``[span class="x"]`` on write and the
    next cycle read that back as literal bracket text.
    """
    first_read = preprocessor.clean_jira_text(stored)
    first_write = preprocessor.markdown_to_jira(first_read)
    second_write = preprocessor.markdown_to_jira(
        preprocessor.clean_jira_text(first_write)
    )

    assert second_write == first_write
    assert "[span" not in first_write
    assert "[/span]" not in first_write
    assert "[ins" not in first_write and "[/ins]" not in first_write


# --------------------------------------------------------------------------
# Confluence must be unaffected
# --------------------------------------------------------------------------


def test_confluence_path_is_untouched():
    """``process_html_content`` must not inherit the Jira-only options.

    The markdownify options and the tag protection live at
    ``_convert_html_to_markdown``, whose only caller in src/ is
    ``preprocessing/jira.py``. ``process_html_content`` - used exclusively by
    ``src/mcp_atlassian/confluence/`` - still calls plain ``md()``, so
    Confluence output keeps markdownify's default escaping and default tag
    handling.
    """
    from mcp_atlassian.preprocessing.confluence import ConfluencePreprocessor

    confluence = ConfluencePreprocessor(base_url="https://example.atlassian.net")
    _, markdown = confluence.process_html_content(
        '<p>a <span class="x">y</span> b and snake_case</p>'
    )

    assert "<span" not in markdown
    assert "y" in markdown
    assert "snake\\_case" in markdown
