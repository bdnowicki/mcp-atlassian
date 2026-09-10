"""Live rendered-HTML tests for PROPX-398 (Jira Server/DC wiki markup).

THE VERIFICATION RULE THIS FILE EXISTS FOR: the stored field LIES. A writer
that compares strings passes while the render is broken, so any claim about
real Jira behaviour has to be checked on rendered HTML.

Rendered HTML now has a first-class read: ``jira_get_rendered_html``
(``IssuesMixin.get_rendered_html``), added because the probes below had to
hand-roll a REST client to see it at all. ``jira_get_issue`` with
``expand=renderedFields`` is still NOT rendered HTML - the payload is
fetched and dropped, and the description comes back converted to Markdown.

The probes below deliberately keep using raw REST - POST a comment carrying
raw wiki markup, then GET
``/rest/api/2/issue/{key}/comment/{id}?expand=renderedBody`` - because a
test cannot verify the read path with the read path. Their independence is
the point, and ``TestGetRenderedHtmlMatchesRawRest`` is what ties the two
channels together, comparing them byte-for-byte on a live instance.

WHAT IS ENFORCED WHERE - read this before trusting a green suite
----------------------------------------------------------------
``TestJiraWikiRender``, ``TestJiraEpicLinkStored`` and
``TestGetRenderedHtmlMatchesRawRest`` need a live Jira
Server/DC instance, so they are skipped three times over: the ``integration``
marker needs ``--integration`` (registered in tests/integration/conftest.py),
the autouse fixture needs ``--use-real-data``, and the credentials have to be
in the environment. Run them with::

    uv run pytest tests/integration --integration --use-real-data

MEASURED, pytest 9.0.2 on 2026-09-10: NO pytest ITEM in this directory runs
under a bare ``uv run pytest`` - not even an unmarked one.
tests/integration/conftest.py skips on ``"integration" in item.keywords``, and
every item collected here carries the directory node's own name as a keyword,
so the sniff catches marker-free tests too. The keywords of the offline test
below are

    ['', 'TestWritePathMatchesRenderedEvidence', ..., 'integration',
     'mcp-atlassian', 'parametrize', 'pytestmark', 'skip', ...,
     'test_jira_wiki_render.py', 'tests']

and as a positive control, a brand-new marker-free test file dropped into this
directory was skipped with "Need --integration option to run".
``uv run pytest tests/integration/test_jira_wiki_render.py -q`` reported
"15 skipped" - zero assertions executed. (That was the item count when the
finding was raised; the count moves with the file, the skip does not.)

That is why the offline evidence pins are ALSO enforced at MODULE IMPORT TIME
by ``enforce_offline_pins()``, called at the very bottom of this module.
Collection imports this module before that conftest hook gets to add its skip
marker (measured: a module-level ``raise`` during
``uv run pytest tests/integration -q`` is reported as "ERROR collecting
tests/integration/test_jira_wiki_render.py" and the run exits non-zero), so
the import-time gate is the only mechanism available from inside a single
file in this directory that a bare ``uv run pytest`` cannot ignore. It is a
deliberate workaround, not a pattern to copy - the handover items below name
the two edits that retire it.

VERIFIED that this reaches CI: .github/workflows/tests.yml:42 runs
``uv run pytest -v --cov=src/mcp_atlassian --cov-report=term-missing`` from
the repo root - no path filter, no ``--integration`` - and
``uv run pytest --collect-only`` from the root collects 25 items out of this
file, i.e. imports it. The gate therefore runs in CI even though every one
of those 25 items is then skipped. It does NOT run for a narrower invocation
like ``uv run pytest tests/unit``, which never imports this module.

So, as of this file:

* THE EMITTED BYTES ARE CI-ENFORCED TWICE - by
  tests/unit/preprocessing/test_markdown_to_jira_braces.py, which runs in CI
  and pins the same shapes this file posts to the renderer, and by the
  import-time gate here, which fails a bare ``uv run pytest`` if the write
  path stops emitting the markup that was actually measured against Jira.
* THE RENDERED CLAIMS ARE STILL ENFORCED BY NOTHING AUTOMATIC. Only the live
  command above re-measures them, and no CI job, git hook or PR template
  requires it - CI has no instance credentials, so it cannot. Someone who
  deletes the brace escape and then "fixes" the unit goldens to match gets a
  green suite with nothing to contradict them EXCEPT the recorded HTML kept
  beside every golden here, and the import-time gate that refuses to let the
  bytes drift away from it silently. Closing the rest of that gap needs the
  AGENTS.md handover item below; a test file cannot do it.

``TestWritePathMatchesRenderedEvidence`` narrows the gap as far as this file
can reach: it keeps the exact markup that was POSTed next to the HTML that
came back for BOTH the correct and the broken form, asserts the write path
still emits it, and needs no network. Its checks are plain ``_check_*``
functions, so the same code runs three times over: at import in every pytest
invocation that collects this directory, as granular test items under
``--integration``, and as the fail-fast prelude to the live probes.

Required before merging any change to
``src/mcp_atlassian/preprocessing/jira.py`` or
``src/mcp_atlassian/preprocessing/base.py``:

  uv run pytest tests/integration/test_jira_wiki_render.py --integration --use-real-data

That command lives in ``LIVE_RENDER_COMMAND`` below and a test pins it into
this docstring, because for the rendered half documentation is still the only
enforcement mechanism.

OPEN HANDOVER ITEMS (each needs a file outside this one)
* AGENTS.md: the pre-merge requirement above is NOT yet written down there
  (measured: ``grep -i render AGENTS.md`` finds nothing). The exact text to
  add is carried verbatim in ``AGENTS_MD_HANDOVER`` below so it can be pasted
  into the "Dev workflow" block without being paraphrased.
* tests/unit/preprocessing/: the PERMANENT fix for the skip is to move
  ``EVIDENCE`` and ``TestWritePathMatchesRenderedEvidence`` into a file under
  tests/unit/preprocessing/ - they need no network and are only skipped
  because of where they live - leaving this file with the live probes alone.
  When that lands, DELETE the import-time gate; it exists only because a
  single file in this directory cannot otherwise execute an assertion.
* tests/integration/conftest.py: skip on the marker
  (``item.get_closest_marker("integration")``), not on the keyword, so a
  marker-free test in this directory can run in CI.
* .github/workflows/tests.yml: no job runs the live half; it cannot, without
  instance credentials, which is why the pre-merge step has to be written
  down for a human.

No test in this repo loads a .env file, so the credentials must arrive as
exported environment variables:

    JIRA_URL, JIRA_PERSONAL_TOKEN, JIRA_SSL_VERIFY
    JIRA_TEST_ISSUE_KEY (default CHSTC-1101), JIRA_TEST_EPIC_KEY (default
    CHSTC-574), JIRA_TEST_EPIC_CHILD_KEY (default CHSTC-1102)

If ``--use-real-data`` is passed while those variables are missing, the live
tests FAIL rather than skip. An operator who asked for live verification and
was told "skipped" would otherwise tick a box that was never checked.

Every live test writes to ONE scratch comment on an authorized CHSTC scratch
issue and deletes it again. The token is never printed.

LAST LIVE RUN: 2026-09-10 against the reference Jira Server/DC instance
(scratch issues CHSTC-1101 and CHSTC-1102), 25 passed. In the same session
BOTH forms of ALL SIX evidence rows - 12 POSTs - were re-measured and the
recorded HTML replaced with the byte-exact ``renderedBody``, so nothing in
this file is an excerpt or an extrapolation any more. That closed the last
extrapolated cell (the escaped fence) and settled L4 - which reversed on the
measurement, see ``installed-macro-pair-kept-verbatim``. Update this line
whenever the live command is re-run, and say what changed if a rendered claim
moved.
"""

import inspect
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass

import pytest

from mcp_atlassian.jira import JiraFetcher
from mcp_atlassian.jira.config import JiraConfig
from mcp_atlassian.preprocessing.jira import JiraPreprocessor

#: The pre-merge invocation for the live half of this file. Kept as a
#: constant so the skip messages, the failure messages and the module
#: docstring cannot drift apart (pinned by
#: ``test_pre_merge_command_stays_discoverable``).
LIVE_RENDER_COMMAND = (
    "uv run pytest tests/integration/test_jira_wiki_render.py"
    " --integration --use-real-data"
)

#: Verbatim text for the AGENTS.md handover item. This file cannot edit
#: AGENTS.md, and the requirement is worthless if it gets paraphrased on the
#: way over, so it is carried here ready to paste into the "Dev workflow"
#: block. Pinned by ``test_agents_md_handover_text_is_carried_verbatim``.
AGENTS_MD_HANDOVER = """\
### Rendered-HTML verification (required for Jira wiki markup changes)

Any change to `src/mcp_atlassian/preprocessing/jira.py` or
`src/mcp_atlassian/preprocessing/base.py` MUST be verified against rendered
HTML on a scratch project before merge. The stored field lies: a writer that
only compares strings passes while the render is broken.

- `jira_get_issue` with `expand=renderedFields` is NOT rendered HTML - it
  returns the description converted back to Markdown. Use
  `jira_get_rendered_html`, which returns Jira's HTML verbatim. The live
  probes still measure the renderer over raw REST instead - POST the wiki
  markup as a comment, then GET
  `/rest/api/2/issue/{key}/comment/{id}?expand=renderedBody` - because a
  test cannot verify the read path with the read path.
- Run the live checks (needs `JIRA_URL` and `JIRA_PERSONAL_TOKEN` exported):

  ```bash
  uv run pytest tests/integration --integration --use-real-data
  ```

- CI cannot run them - it has no instance credentials - so a green
  `uv run pytest` proves nothing about real Jira rendering.
"""


@dataclass(frozen=True)
class RenderedEvidence:
    """One markdown source measured against the real Jira wiki renderer.

    ``wiki`` is the exact byte string that was POSTed as a comment body and
    ``rendered`` is what came back in ``renderedBody``. ``wiki_if_regressed``
    is what the write path emitted BEFORE the PROPX-398 brace escape (or, for
    a negative control, what it would emit if the escape hook were moved to
    the wrong place) and ``rendered_if_regressed`` is what the renderer did
    with that.

    Storing the pair is the whole point: an offline test can only prove that
    the emitted bytes are unchanged, so the bytes have to be kept next to
    what the renderer was observed to do with them.
    """

    id: str
    markdown: str
    wiki: str
    rendered: str
    wiki_if_regressed: str
    rendered_if_regressed: str
    damage: str


#: Measured on Jira Server/DC 2026-09-10 against scratch issue CHSTC-1101, by
#: POSTing ``wiki`` as a raw comment body and reading ``renderedBody`` back.
#: Every ``rendered`` and ``rendered_if_regressed`` string below is the
#: BYTE-EXACT ``renderedBody`` of that POST, re-measured for both forms of all
#: six rows in one probe run - not an excerpt, not a prediction. Nothing here
#: is extrapolated any more; the two cells that used to be (the escaped-fence
#: control and L4's escaped macro) were measured in that run.
#:
#: The live probes still assert the damage CLASS rather than byte equality
#: with these strings, deliberately: the exact extent depends on the
#: surrounding document and Jira's own whitespace, so a byte assertion would
#: fail on an instance upgrade without anything being broken. The strings are
#: here to be READ by whoever is tempted to change a golden.
EVIDENCE: dict[str, RenderedEvidence] = {
    "bullet-list-with-escaped-monospace": RenderedEvidence(
        id="bullet-list-with-escaped-monospace",
        markdown=(
            "- item one with `GET /issue/{issueKey}`\n"
            "- item two plain\n"
            "- item three plain"
        ),
        wiki=(
            "* item one with {{GET /issue/\\{issueKey}}}\n"
            "* item two plain\n"
            "* item three plain"
        ),
        rendered=(
            "<ul>\n"
            "\t<li>item one with <tt>GET /issue/{issueKey</tt>}</li>\n"
            "\t<li>item two plain</li>\n"
            "\t<li>item three plain</li>\n"
            "</ul>\n"
        ),
        wiki_if_regressed=(
            "* item one with {{GET /issue/{issueKey}}}\n"
            "* item two plain\n"
            "* item three plain"
        ),
        rendered_if_regressed=(
            "<ul>\n"
            "\t<li>item one with {{GET /issue/\n{issueKey}\n<p>}}</p></li>\n"
            "\t<li>item two plain</li>\n"
            "\t<li>item three plain</li>\n"
            "</ul>\n"
        ),
        damage=(
            "Unescaped, the brace promoted the rest of the item to a macro "
            "block and injected a <p> inside the <li>; the original report "
            "lost two of three list items and swallowed two whole sections. "
            "The one stray '}' outside the <tt> in the escaped form is the "
            "accepted cosmetic residual of content that ends in '}' "
            "colliding with the '}}' terminator - no escaping fixes it and "
            "it is not corruption."
        ),
    ),
    "escaped-brace-in-prose": RenderedEvidence(
        id="escaped-brace-in-prose",
        markdown="before a{b}c after",
        wiki="before a\\{b}c after",
        rendered="<p>before a{b}c after</p>",
        wiki_if_regressed="before a{b}c after",
        rendered_if_regressed="<p>before a</p>\n{b}\n<p>c after</p>",
        damage=(
            "Unescaped, one sentence was torn into three fragments with "
            "'{b}' promoted to a macro block. Bare braces in PROSE break "
            "too, which is why the escape covers every text node and not "
            "only monospace spans."
        ),
    ),
    "fenced-code-braces-untouched": RenderedEvidence(
        id="fenced-code-braces-untouched",
        markdown="```java\nx = {id}\n```",
        wiki="{code:java}x = {id}\n{code}",
        rendered=(
            '<div class="code panel" style="border-width: 1px;">'
            '<div class="codeContent panelContent">\n'
            '<pre class="code-java">x = {id}\n</pre>\n'
            "</div></div>"
        ),
        wiki_if_regressed="{code:java}x = \\{id}\n{code}",
        rendered_if_regressed=(
            '<div class="code panel" style="border-width: 1px;">'
            '<div class="codeContent panelContent">\n'
            '<pre class="code-java">x = \\{id}\n</pre>\n'
            "</div></div>"
        ),
        damage=(
            "NEGATIVE CONTROL. Fenced content is extracted into a "
            "placeholder before the escape runs, so the brace must survive "
            "verbatim; a failure here means the escape hook sits in the "
            "wrong place. The missing newline after the opening macro is a "
            "pre-existing quirk - assert the current bytes, do not fix it. "
            "PROVENANCE: this row's 'rendered_if_regressed' was the one "
            "EXTRAPOLATED cell in the file and it is now MEASURED "
            "(2026-09-10): posting an escaped fence renders the backslash "
            "VERBATIM inside the code panel, 'x = \\{id}'. So the negative "
            "control is not merely tidy - letting the escape reach fenced "
            "content would show every reader a literal backslash in their "
            "code, and no reader could tell it was ours."
        ),
    ),
    "installed-macro-pair-kept-verbatim": RenderedEvidence(
        id="installed-macro-pair-kept-verbatim",
        markdown="literal {color:red}x{color} typed",
        wiki="literal {color:red}x{color} typed",
        rendered='<p>literal <font color="red">x</font> typed</p>',
        wiki_if_regressed="literal \\{color:red}x\\{color} typed",
        rendered_if_regressed="<p>literal {color:red}x{color} typed</p>",
        damage=(
            "REVISED in repair round 2, and this row is the one that "
            "changed direction, so read the whole note. L4 asked whether "
            "'\\{color:red}' renders literally; RE-MEASURED here on "
            "2026-09-10 it does - '<p>literal {color:red}x{color} "
            "typed</p>' - so R13's read-path contract (do not consume an "
            "escape you did not author) stands unchanged. What the same "
            "probe also measured is that the UNESCAPED pair is real "
            "markup: '<font color=\"red\">x</font>'. Escaping a complete "
            "pair of a macro this instance actually installs therefore "
            "DESTROYS markup - the colour is gone for good - which is why "
            "the write path now exempts it and why 'rendered_if_regressed' "
            "here looks harmless but is the lossy form. The accepted cost "
            "is the other direction: a user who types an installed macro "
            "name literally in prose gets markup. Their escape hatches are "
            "measured and intact - typing '\\{color:red}' passes through "
            "the (?<!\\\\) lookbehind and renders literally, and "
            "DISABLE_JIRA_MARKUP_TRANSLATION=true bypasses the converter "
            "entirely. The exemption is NOT the 'braces that look like a "
            "macro' guess the plan rejected: it is keyed to the macro names "
            "measured to be installed on this instance, and the next two "
            "rows are the negative controls that keep it honest."
        ),
    ),
    "not-installed-macro-escaped": RenderedEvidence(
        id="not-installed-macro-escaped",
        markdown="see {toc:maxLevel=2} here",
        wiki="see \\{toc:maxLevel=2} here",
        rendered="<p>see {toc:maxLevel=2} here</p>",
        wiki_if_regressed="see {toc:maxLevel=2} here",
        rendered_if_regressed="<p>see </p>\n{toc:maxLevel=2}\n<p> here</p>",
        damage=(
            "NEGATIVE CONTROL for the macro exemption above. A macro name "
            "this instance does NOT install tears the paragraph into three "
            "byte for byte like an unknown '{b}' - measured 2026-09-10 - so "
            "escaping it is the BETTER render, not a loss of markup. If the "
            "exemption ever widens to 'anything shaped like a macro', this "
            "row fails."
        ),
    ),
    "unmatched-macro-opener-escaped": RenderedEvidence(
        id="unmatched-macro-opener-escaped",
        markdown="use {code} for code",
        wiki="use \\{code} for code",
        rendered="<p>use {code} for code</p>",
        wiki_if_regressed="use {code} for code",
        rendered_if_regressed=(
            '<p>use </p>\n<div class="code panel" style="border-width: 1px;">'
            '<div class="codeContent panelContent">\n<pre class="code-java">'
            "</pre>\n</div></div>\n<p> for code</p>"
        ),
        damage=(
            "NEGATIVE CONTROL and the most destructive shape measured "
            "2026-09-10: an INSTALLED macro name with an UNMATCHED "
            "delimiter is prose, and unescaped it both tears the paragraph "
            "and opens an empty code panel. In a document rather than a "
            "one-line comment that panel keeps swallowing content, which is "
            "the class of damage PROPX-398 was opened for. So the exemption "
            "must require a COMPLETE pair, never a lone opener."
        ),
    ),
}


class OfflineEvidenceRegressionError(RuntimeError):
    """Raised when the recorded rendered evidence no longer matches the code.

    Deliberately raised at module import so that it surfaces as a pytest
    COLLECTION ERROR. Every pytest item in tests/integration/ is skipped
    unless ``--integration`` is passed, so a normal test assertion in this
    directory can never fail a bare ``uv run pytest``.
    """


def _check_write_path_emits_measured_markup(evidence: RenderedEvidence) -> None:
    """The write path must still emit exactly what was measured.

    Regression guard for PROPX-398: removing the brace escape or its call
    site fails this check, in the file that records what the renderer did
    with both the escaped and the unescaped bytes. The CI-running twin of
    this assertion (bytes only, no rendered evidence) lives in
    tests/unit/preprocessing/test_markdown_to_jira_braces.py.
    """
    actual = JiraPreprocessor().markdown_to_jira(evidence.markdown)

    assert actual == evidence.wiki, (
        "The write path no longer emits the markup that was measured "
        f"against the real Jira renderer (case {evidence.id!r}).\n"
        f"  markdown in  : {evidence.markdown!r}\n"
        f"  emitted now  : {actual!r}\n"
        f"  measured good: {evidence.wiki!r}\n"
        f"    rendered as: {evidence.rendered!r}\n"
        f"  known broken : {evidence.wiki_if_regressed!r}\n"
        f"    rendered as: {evidence.rendered_if_regressed!r}\n"
        f"  damage       : {evidence.damage}\n"
        "Do NOT update this golden to match the new output. The strings "
        "above are what the renderer actually did, so a mismatch means "
        "either the write path regressed or the rendered claim needs "
        "re-measuring:\n"
        f"    {LIVE_RENDER_COMMAND}"
    )


def _check_row_is_not_self_satisfying(
    evidence: RenderedEvidence, rows: Mapping[str, RenderedEvidence]
) -> None:
    """The good and the broken markup of a row must actually differ.

    Without this, "fixing" a failing row by copying ``wiki_if_regressed``
    into ``wiki`` would leave a green, vacuous pin.
    """
    assert evidence.wiki != evidence.wiki_if_regressed, (
        f"evidence row {evidence.id!r} records the same markup as good and "
        "as regressed, so its pin proves nothing"
    )
    assert evidence.rendered != evidence.rendered_if_regressed, (
        f"evidence row {evidence.id!r} records the same rendered HTML for "
        "the good and the regressed markup, so its pin proves nothing about "
        "the damage"
    )
    assert rows[evidence.id] is evidence, (
        f"evidence row {evidence.id!r} is not reachable under its own id"
    )


def _check_every_live_render_probe_has_an_offline_pin() -> None:
    """Every live probe must post markup that is also pinned offline.

    Both directions are checked, because either gap re-opens the finding
    this file was repaired for: a live probe with no offline pin is invisible
    to CI, and an offline row that no live probe posts is a claim about the
    renderer that nothing ever re-measures.
    """
    source = inspect.getsource(TestJiraWikiRender)

    for key in EVIDENCE:
        assert f'EVIDENCE["{key}"]' in source, (
            f"evidence row {key!r} is never posted to the renderer by "
            "TestJiraWikiRender, so its recorded HTML is unverifiable"
        )

    probes = [
        name
        for name, _ in inspect.getmembers(TestJiraWikiRender, inspect.isfunction)
        if name.startswith("test_")
    ]
    assert probes, "TestJiraWikiRender has no live probes"
    for name in probes:
        body = inspect.getsource(getattr(TestJiraWikiRender, name))
        assert "EVIDENCE[" in body, (
            f"{name} posts markup to the renderer without an EVIDENCE "
            "row, so CI cannot detect a regression in what it posts"
        )


def _check_pre_merge_command_stays_discoverable() -> None:
    """The live-run command and the handover items must stay documented.

    The live probes are the only check that satisfies the ticket's
    verification rule, and nothing in this repo runs them, so the command
    that does - plus the note saying where the requirement to run it belongs
    - IS this file's enforcement mechanism for the rendered half. Losing it
    silently is the regression; this check makes that loud.
    """
    doc = sys.modules[__name__].__doc__ or ""
    normalised = " ".join(doc.split())

    assert LIVE_RENDER_COMMAND in normalised, (
        "the module docstring no longer documents how to run the live "
        f"rendered-HTML checks; expected to find: {LIVE_RENDER_COMMAND}"
    )
    assert "AGENTS.md" in doc, (
        "the docstring must keep naming AGENTS.md as the place the "
        "required pre-merge step belongs, so the handover item is not "
        "lost when this file is next edited"
    )
    assert "AGENTS_MD_HANDOVER" in doc, (
        "the docstring must point at AGENTS_MD_HANDOVER, or the paste-ready "
        "pre-merge text is unfindable by whoever owns AGENTS.md"
    )
    assert "test_markdown_to_jira_braces.py" in doc, (
        "the docstring must keep naming the CI-enforced unit goldens, "
        "because that file - not this one - is what a bare "
        "`uv run pytest` actually checks"
    )
    assert "tests/unit/preprocessing/" in doc, (
        "the docstring must keep recording that moving the offline pins to "
        "tests/unit/preprocessing/ is the permanent fix for the "
        "directory-wide skip, and that the import-time gate goes away with it"
    )


def enforce_offline_pins(
    rows: Mapping[str, RenderedEvidence] | None = None,
) -> None:
    """Run every offline evidence check, reporting all failures at once.

    Called at module import (see the call site below) so that the pins
    execute even under a bare ``uv run pytest``, where every item in this
    directory is skipped by tests/integration/conftest.py. Also called by
    ``TestWritePathMatchesRenderedEvidence`` for granular reporting, and with
    an explicit ``rows`` mapping by the two tests that prove this gate is not
    vacuous.

    Args:
        rows: evidence to check. ``None`` means the module's own ``EVIDENCE``
            plus the structural checks over this file; an explicit mapping
            checks only those rows, which is what the gate's own regression
            tests pass in.

    Raises:
        OfflineEvidenceRegressionError: if any check fails. The message carries
            every failure, because a partial answer sends the reader back to
            the renderer twice.
    """
    structural = rows is None
    rows = EVIDENCE if rows is None else rows

    failures: list[str] = []
    for evidence in rows.values():
        try:
            _check_write_path_emits_measured_markup(evidence)
        except AssertionError as exc:
            failures.append(f"[{evidence.id}] {exc}")
        try:
            _check_row_is_not_self_satisfying(evidence, rows)
        except AssertionError as exc:
            failures.append(f"[{evidence.id}] {exc}")

    if structural:
        for structural_check in (
            _check_every_live_render_probe_has_an_offline_pin,
            _check_pre_merge_command_stays_discoverable,
        ):
            try:
                structural_check()
            except AssertionError as exc:
                failures.append(f"[{structural_check.__name__}] {exc}")

    if failures:
        message = (
            f"PROPX-398 rendered-evidence pins FAILED at import of {__name__}."
            "\n\n" + "\n\n".join(failures) + "\n\n"
            "This is reported as a pytest COLLECTION ERROR on purpose: "
            "tests/integration/conftest.py skips every item in this "
            "directory unless --integration is passed, so a normal "
            "assertion here cannot fail a bare `uv run pytest`. Pass "
            "--continue-on-collection-errors to see the rest of the suite "
            "while you fix this."
        )
        raise OfflineEvidenceRegressionError(message)


def _strip_tags(html: str) -> str:
    """Return the text content of a rendered HTML fragment."""
    return re.sub(r"<[^>]+>", "", html)


def _require_env(name: str) -> str:
    """Return an environment variable, failing loudly when it is missing.

    Only reached after ``--use-real-data`` was passed, i.e. after an operator
    explicitly asked for live verification, so a missing variable is an
    operator error rather than an absent capability. Skipping here would
    report "skipped" for a rendered check the operator believes they just
    ran - and "skipped" reads as green, which is exactly the failure mode
    this file's enforcement section exists to prevent.
    """
    value = os.getenv(name)
    if not value:
        pytest.fail(
            f"{name} is not set, so the live rendered-HTML checks did NOT "
            "run even though --use-real-data was requested. Export JIRA_URL, "
            "JIRA_PERSONAL_TOKEN and JIRA_SSL_VERIFY, then re-run:\n"
            f"    {LIVE_RENDER_COMMAND}"
        )
    return value


@pytest.fixture
def base_url():
    """Base URL of the configured Jira Server/DC instance."""
    return _require_env("JIRA_URL").rstrip("/")


@pytest.fixture
def rest():
    """Raw REST session against the configured Jira Server/DC instance."""
    token = _require_env("JIRA_PERSONAL_TOKEN")

    import requests

    verify = os.getenv("JIRA_SSL_VERIFY", "true").lower() not in (
        "false",
        "0",
        "no",
    )
    if not verify:
        import urllib3

        urllib3.disable_warnings()

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    )
    session.verify = verify
    return session


@pytest.fixture
def issue_key():
    """Scratch issue that the render probes comment on."""
    return os.getenv("JIRA_TEST_ISSUE_KEY", "CHSTC-1101")


@pytest.fixture
def render_wiki(rest, base_url, issue_key):
    """Return a callable that renders raw wiki markup to HTML.

    Posts the markup as a comment, reads ``renderedBody`` back, then
    deletes the comment so the scratch issue is left as it was found.
    """
    created: list[str] = []

    def _render(wiki: str) -> str:
        response = rest.post(
            f"{base_url}/rest/api/2/issue/{issue_key}/comment",
            data=json.dumps({"body": wiki}),
            timeout=60,
        )
        response.raise_for_status()
        comment_id = response.json()["id"]
        created.append(comment_id)
        rendered = rest.get(
            f"{base_url}/rest/api/2/issue/{issue_key}/comment/{comment_id}",
            params={"expand": "renderedBody"},
            timeout=60,
        )
        rendered.raise_for_status()
        return rendered.json()["renderedBody"]

    yield _render

    for comment_id in created:
        try:
            rest.delete(
                f"{base_url}/rest/api/2/issue/{issue_key}/comment/{comment_id}",
                timeout=60,
            )
        except Exception as exc:  # best-effort cleanup only
            print(f"could not delete scratch comment {comment_id}: {exc}")


@pytest.mark.integration
class TestJiraWikiRender:
    """Assert PROPX-398's write-path output against the real wiki renderer."""

    @pytest.fixture(autouse=True)
    def skip_without_real_data(self, request):
        """Skip unless --use-real-data is provided."""
        if not request.config.getoption("--use-real-data", default=False):
            pytest.skip(
                "Live rendered-HTML checks NOT run - they need "
                "--use-real-data and live Jira Server/DC credentials. "
                "Nothing else re-measures the rendered output, so this skip "
                "is an unchecked claim, not a pass. Required before merging "
                "a change to preprocessing/jira.py: "
                f"{LIVE_RENDER_COMMAND}"
            )

    @pytest.fixture
    def preprocessor(self):
        return JiraPreprocessor()

    def test_render_no_macro_block_in_list(self, preprocessor, render_wiki):
        """RENDERED: three clean <li>, a <tt> span, and no injected <p>.

        Asserts the damage CLASS, not the exact extent: the unescaped form
        swallowed following sections in the original report, but how much
        depends on the surrounding document. The recorded HTML for both the
        escaped and the unescaped form is in ``EVIDENCE`` and pinned offline
        by ``TestWritePathMatchesRenderedEvidence``.
        """
        evidence = EVIDENCE["bullet-list-with-escaped-monospace"]
        wiki = preprocessor.markdown_to_jira(evidence.markdown)
        assert wiki == evidence.wiki, wiki

        html = render_wiki(wiki)

        assert html.count("<li>") == 3, html
        # A correctly rendered bullet list carries no <p> at all; the
        # unescaped form injects one where the macro block tore the item.
        assert "<p>" not in html, html
        assert "<tt>" in html, html
        assert "{{" not in html, html
        assert 'class="error"' not in html, html
        assert "{issueKey" in _strip_tags(html), html

    def test_render_prose_paragraph_intact(self, preprocessor, render_wiki):
        """RENDERED: '<p>before a{b}c after</p>' - one paragraph, brace literal.

        Unescaped, the same sentence rendered as three fragments with '{b}'
        promoted to a macro block (recorded in ``EVIDENCE``).
        """
        evidence = EVIDENCE["escaped-brace-in-prose"]
        wiki = preprocessor.markdown_to_jira(evidence.markdown)
        assert wiki == evidence.wiki, wiki

        html = render_wiki(wiki)

        assert html.count("<p>") == 1, html
        assert 'class="error"' not in html, html
        text = _strip_tags(html)
        assert "before a{b}c after" in text, text

    def test_render_fenced_brace_intact(self, preprocessor, render_wiki):
        """RENDERED: '<pre class="code-java">x = {id}</pre>' - brace verbatim.

        NEGATIVE CONTROL: fenced content is never escaped, because it is
        extracted into a placeholder before the escape point.
        """
        evidence = EVIDENCE["fenced-code-braces-untouched"]
        wiki = preprocessor.markdown_to_jira(evidence.markdown)
        assert wiki == evidence.wiki, wiki

        html = render_wiki(wiki)

        assert "code-java" in html, html
        assert "x = {id}" in _strip_tags(html), html
        assert "\\{id}" not in html, html

    def test_render_installed_macro_pair_is_markup(self, preprocessor, render_wiki):
        """RENDERED: both halves of the L4 question, re-measured together.

        This is the probe that settled L4 and then reversed the conclusion
        the plan had extrapolated from it, so it asserts BOTH forms in one
        test - the recorded pair is only trustworthy if the same run
        measured both:

        * what the write path emits now (an installed macro pair, kept
          verbatim) must render as real markup: '<font color="red">x</font>';
        * the escaped form must still render as LITERAL text, because R13's
          read-path contract - never consume an escape you did not author -
          depends on that and on nothing else.

        Escaping the pair would therefore not be "safer", it would delete
        the colour permanently. See this row's ``damage`` for the trade-off
        that buys and for the author's escape hatches.
        """
        evidence = EVIDENCE["installed-macro-pair-kept-verbatim"]
        wiki = preprocessor.markdown_to_jira(evidence.markdown)
        assert wiki == evidence.wiki, wiki

        html = render_wiki(wiki)
        assert 'color="red"' in html, html
        assert "{color:red}" not in _strip_tags(html), html

        # The escape hatch: hand-typed '\{color:red}' stays literal.
        escaped_html = render_wiki(evidence.wiki_if_regressed)
        escaped_text = _strip_tags(escaped_html)
        assert "{color:red}" in escaped_text, escaped_html
        assert "<font" not in escaped_html, escaped_html
        # And the backslash must not leak into the rendered text.
        assert "\\{" not in escaped_text, escaped_text

    def test_render_not_installed_macro_is_literal(self, preprocessor, render_wiki):
        """RENDERED: '<p>see {toc:maxLevel=2} here</p>' - one intact paragraph.

        NEGATIVE CONTROL for the macro exemption: a name this instance does
        not install must still be escaped, because unescaped it tears the
        paragraph into three exactly like an unknown '{b}'. Both forms are
        asserted, because the exemption widening to "anything shaped like a
        macro" is the regression this row exists to catch and only the
        broken form's render proves the escape is buying anything.
        """
        evidence = EVIDENCE["not-installed-macro-escaped"]
        wiki = preprocessor.markdown_to_jira(evidence.markdown)
        assert wiki == evidence.wiki, wiki

        html = render_wiki(wiki)
        assert html.count("<p>") == 1, html
        assert "{toc:maxLevel=2}" in _strip_tags(html), html
        assert "\\{" not in _strip_tags(html), html

        # The unescaped form is the measured damage: three fragments.
        broken = render_wiki(evidence.wiki_if_regressed)
        assert broken.count("<p>") == 2, broken

    def test_render_unmatched_macro_opener_is_literal(self, preprocessor, render_wiki):
        """RENDERED: '<p>use {code} for code</p>' - no code panel injected.

        NEGATIVE CONTROL: an INSTALLED macro name with an UNMATCHED
        delimiter is prose, so the exemption must require a complete pair.
        Unescaped this was measured to tear the paragraph AND open an empty
        code panel, which in a real document keeps swallowing content.
        """
        evidence = EVIDENCE["unmatched-macro-opener-escaped"]
        wiki = preprocessor.markdown_to_jira(evidence.markdown)
        assert wiki == evidence.wiki, wiki

        html = render_wiki(wiki)
        assert html.count("<p>") == 1, html
        assert "code panel" not in html, html
        assert "{code}" in _strip_tags(html), html
        assert "\\{" not in _strip_tags(html), html


class TestWritePathMatchesRenderedEvidence:
    """Offline evidence pins - no marker, no flags, no network.

    These assert that the write path still emits the byte-exact markup that
    was POSTed to the real renderer, and they keep that markup next to the
    HTML which came back for both the correct and the broken form. Someone
    who "fixes" a golden to match a removed escape has to walk past the
    measured output of the broken form to do it here, and the failure message
    tells them to re-measure rather than edit.

    These ITEMS still do not run under a bare ``uv run pytest`` - nothing in
    tests/integration/ does, measured, see the module docstring - so the same
    ``_check_*`` functions are additionally invoked by
    ``enforce_offline_pins()`` at module import, which a bare run cannot
    skip. The class stays because it gives per-case reporting under
    ``--integration``, and because ``test_offline_pins_ran_at_import_time``
    plus the two vacuity tests are the regression guard on the gate itself.

    What they still cannot do is prove Jira RENDERS those bytes the same way.
    Nothing offline can. Only ``LIVE_RENDER_COMMAND`` does that.
    """

    @pytest.mark.parametrize("evidence", list(EVIDENCE.values()), ids=list(EVIDENCE))
    def test_converter_still_emits_the_measured_markup(self, evidence):
        """Per-case view of the import-time byte pin."""
        _check_write_path_emits_measured_markup(evidence)

    @pytest.mark.parametrize("evidence", list(EVIDENCE.values()), ids=list(EVIDENCE))
    def test_evidence_rows_are_not_self_satisfying(self, evidence):
        """Per-case view of the import-time anti-vacuity pin."""
        _check_row_is_not_self_satisfying(evidence, EVIDENCE)

    def test_every_live_render_probe_has_an_offline_pin(self):
        """Per-case view of the import-time probe/pin correspondence check."""
        _check_every_live_render_probe_has_an_offline_pin()

    def test_pre_merge_command_stays_discoverable(self):
        """Per-case view of the import-time documentation check."""
        _check_pre_merge_command_stays_discoverable()

    def test_offline_pins_ran_at_import_time(self):
        """The import-time gate must stay wired up.

        REGRESSION TEST for the finding that repaired this file: the offline
        pins were added as a CI fallback but sat in a directory where
        ``pytest_collection_modifyitems`` skips every item, so they never
        executed - ``uv run pytest tests/integration/test_jira_wiki_render.py
        -q`` reported "15 skipped". ``enforce_offline_pins()`` at module
        scope is the only assertion site here that a bare ``uv run pytest``
        cannot skip, and deleting that call would return the file to "15
        skipped" with no other test noticing.
        """
        assert _IMPORT_TIME_GATE_RAN is True

        source = inspect.getsource(sys.modules[__name__])
        assert "\nenforce_offline_pins()\n" in source, (
            "enforce_offline_pins() is no longer called at module scope, so "
            "no assertion in this file runs under a bare `uv run pytest` "
            "(every item in tests/integration/ is skipped without "
            "--integration). Either restore the call or move the offline "
            "pins to tests/unit/preprocessing/, where they run as ordinary "
            "test items."
        )

    def test_import_time_gate_catches_a_write_path_regression(self):
        """The gate must fail on the markup that was measured as broken.

        Feeds the gate a row whose ``wiki`` is the recorded PRE-FIX output.
        A gate that passed this would also pass a removed brace escape,
        which is the whole regression it exists to catch - so this is what
        stops the import-time call becoming decoration.
        """
        good = EVIDENCE["escaped-brace-in-prose"]
        regressed = {
            good.id: RenderedEvidence(
                id=good.id,
                markdown=good.markdown,
                wiki=good.wiki_if_regressed,
                rendered=good.rendered_if_regressed,
                wiki_if_regressed=good.wiki_if_regressed,
                rendered_if_regressed=good.rendered_if_regressed,
                damage=good.damage,
            )
        }

        with pytest.raises(OfflineEvidenceRegressionError) as excinfo:
            enforce_offline_pins(regressed)

        message = str(excinfo.value)
        assert good.id in message
        # The reader must be told how to re-measure, not merely that two
        # strings differed.
        assert LIVE_RENDER_COMMAND in message
        # Both failures are reported together, not just the first: the byte
        # mismatch AND the row having become self-satisfying.
        assert "emitted now" in message
        assert "proves nothing" in message

    def test_import_time_gate_catches_a_self_satisfying_row(self):
        """The gate must reject a row that records no damage.

        Copying ``wiki`` into ``wiki_if_regressed`` is the cheapest way to
        make a failing pin green, so it has to be an error in its own right
        even when the emitted bytes are still correct.
        """
        good = EVIDENCE["escaped-brace-in-prose"]
        vacuous = {
            good.id: RenderedEvidence(
                id=good.id,
                markdown=good.markdown,
                wiki=good.wiki,
                rendered=good.rendered,
                wiki_if_regressed=good.wiki,
                rendered_if_regressed=good.rendered,
                damage=good.damage,
            )
        }

        with pytest.raises(OfflineEvidenceRegressionError, match="proves nothing"):
            enforce_offline_pins(vacuous)

    def test_agents_md_handover_text_is_carried_verbatim(self):
        """The AGENTS.md pre-merge text must stay paste-ready.

        This file cannot edit AGENTS.md, and the requirement is worthless if
        it gets paraphrased on the way over, so the exact wording lives in
        ``AGENTS_MD_HANDOVER``. The load-bearing facts are: which source
        files trigger the requirement, that ``expand=renderedFields`` is NOT
        rendered HTML, where rendered HTML actually comes from, and the
        invocation that measures it.
        """
        text = AGENTS_MD_HANDOVER

        assert "src/mcp_atlassian/preprocessing/jira.py" in text
        assert "src/mcp_atlassian/preprocessing/base.py" in text
        assert "`expand=renderedFields` is NOT rendered HTML" in text
        assert "renderedBody" in text
        assert "uv run pytest tests/integration --integration --use-real-data" in text


@pytest.mark.integration
class TestJiraEpicLinkStored:
    """Live counterpart to the link_issue_to_epic read-back unit tests."""

    @pytest.fixture(autouse=True)
    def skip_without_real_data(self, request):
        """Skip unless --use-real-data is provided."""
        if not request.config.getoption("--use-real-data", default=False):
            pytest.skip(
                f"Live epic-link check needs --use-real-data; run {LIVE_RENDER_COMMAND}"
            )

    @pytest.fixture
    def jira_client(self):
        """Real Jira client built from the environment."""
        _require_env("JIRA_URL")
        return JiraFetcher(config=JiraConfig.from_env())

    def test_render_epic_link_actually_stored(self, jira_client):
        """link_issue_to_epic must leave the Epic Link field actually SET.

        PROPX-398's headline defect: the tool reported "has been linked to
        epic" while raw REST showed parent null, customfield_10006 null,
        ``updated`` identical to ``created`` and a changelog with zero
        entries. Asserting over the raw library client (not the tool's own
        response) is the whole point - the response was the thing that lied.

        Uses existing scratch issues rather than creating any: on this
        instance issue type Task requires customfield_10101 (Tempo Team) set
        to "Chase Debit Team", which is a project-specific precondition, not
        something this test should encode.
        """
        issue_key = os.getenv("JIRA_TEST_EPIC_CHILD_KEY", "CHSTC-1102")
        epic_key = os.getenv("JIRA_TEST_EPIC_KEY", "CHSTC-574")

        jira_client.link_issue_to_epic(issue_key, epic_key)

        field_ids = jira_client.get_field_ids_to_epic()
        epic_link_field = field_ids.get("epic_link")
        assert epic_link_field, f"no epic_link field discovered: {field_ids}"

        stored = jira_client.jira.get_issue(issue_key, fields=epic_link_field)
        assert stored["fields"][epic_link_field] == epic_key, stored["fields"]


@pytest.mark.integration
class TestGetRenderedHtmlMatchesRawRest:
    """``get_rendered_html`` must be a faithful substitute for raw REST.

    Every other live probe in this file reaches the renderer through a
    hand-rolled ``requests`` session, because until now that was the only
    way to see rendered HTML at all - ``get_issue`` with
    ``expand="renderedFields"`` fetches the payload and then drops it, since
    no model reads that key.

    ``get_rendered_html`` exists to remove that hand-rolled step, and it is
    only worth having if it returns the SAME bytes the raw channel does.
    A near-match would be worse than nothing: it would make the tool a
    third rendering to reconcile instead of one fewer, while looking like
    verification. So these tests compare the two channels directly rather
    than asserting that the output merely "looks like HTML".
    """

    @pytest.fixture(autouse=True)
    def skip_without_real_data(self, request):
        """Skip unless --use-real-data is provided."""
        if not request.config.getoption("--use-real-data", default=False):
            pytest.skip(
                "Live rendered-read cross-check NOT run - it needs "
                "--use-real-data and live Jira Server/DC credentials. "
                "Nothing offline can compare the two channels, so this skip "
                "is an unchecked claim, not a pass. Run: "
                f"{LIVE_RENDER_COMMAND}"
            )

    @pytest.fixture
    def fetcher(self):
        """Real Jira client built from the environment."""
        _require_env("JIRA_URL")
        return JiraFetcher(config=JiraConfig.from_env())

    def test_description_matches_the_raw_rest_channel(
        self, fetcher, rest, base_url, issue_key
    ):
        """Byte-for-byte, not "equivalent"."""
        raw = rest.get(
            f"{base_url}/rest/api/2/issue/{issue_key}",
            params={"expand": "renderedFields", "fields": "description"},
            timeout=60,
        )
        raw.raise_for_status()
        expected = raw.json()["renderedFields"]["description"]

        result = fetcher.get_rendered_html(issue_key)

        assert result["fields"]["description"] == expected
        assert result["key"] == issue_key
        assert result["browse_url"].endswith(f"/browse/{issue_key}")

    def test_comment_body_matches_the_raw_rest_channel(
        self, fetcher, render_wiki, issue_key
    ):
        """The comment path is the one a writer actually verifies through.

        ``render_wiki`` reads ``renderedBody`` off the comment resource,
        while this method reads ``renderedFields.comment.comments[].body``
        off the issue. They are two different endpoints, so agreement is a
        measurement rather than an assumption.
        """
        wiki = (
            "h3. Rendered read cross-check\n\n"
            "*bold* and \\{not a macro} and {{monospace}}\n\n"
            "* one\n* two\n"
        )
        expected = render_wiki(wiki)

        result = fetcher.get_rendered_html(
            issue_key, include_comments=True, comment_limit=1
        )

        assert len(result["comments"]) == 1
        assert result["comments"][0]["body"] == expected

    def test_get_issue_still_returns_markdown_for_the_same_field(
        self, fetcher, issue_key
    ):
        """Pins the asymmetry that justifies two separate reads.

        If ``get_issue`` ever starts returning HTML, or this method ever
        starts returning Markdown, the two contracts have collided and a
        caller cannot tell which shape they are asserting on.
        """
        rendered = fetcher.get_rendered_html(issue_key)["fields"]["description"]
        markdown = fetcher.get_issue(issue_key, fields=["description"]).description

        assert "<" in rendered, rendered[:200]
        assert rendered != markdown

    def test_expand_rendered_fields_on_get_issue_is_still_a_no_op(
        self, fetcher, issue_key
    ):
        """The defect this tool works around, pinned against the instance.

        ``expand="renderedFields"`` is accepted and changes nothing about
        what ``get_issue`` returns. It is documented as a no-op in the tool
        description; this measures that it still is, so the documentation
        cannot quietly become wrong.
        """
        plain = fetcher.get_issue(issue_key, fields=["description"]).description
        expanded = fetcher.get_issue(
            issue_key, fields=["description"], expand="renderedFields"
        ).description

        assert expanded == plain

    def test_missing_issue_raises_value_error(self, fetcher):
        """A failed verification read must raise, not return an empty dict.

        Measured 2026-09-10: without the explicit mapping this surfaced as a
        bare ``HTTPError``, which the docstring did not promise.
        """
        project = os.getenv("JIRA_TEST_ISSUE_KEY", "CHSTC-1101").split("-")[0]

        with pytest.raises(ValueError, match="not found"):
            fetcher.get_rendered_html(f"{project}-999999")


# The offline pins are enforced HERE, at import, and not only by the test
# class above. MEASURED (pytest 9.0.2, 2026-09-10):
# `uv run pytest tests/integration/test_jira_wiki_render.py -q` reported
# "15 skipped" because tests/integration/conftest.py skips on
# `"integration" in item.keywords` and the directory node's name is itself a
# keyword - so a marker-free test in this directory does not run either.
# Collection still IMPORTS the module first, so this call is the one
# assertion site in this file that a bare `uv run pytest` cannot skip.
# It sits at the BOTTOM of the module because the structural checks inspect
# TestJiraWikiRender, which has to exist first.
# Retire it by moving EVIDENCE and TestWritePathMatchesRenderedEvidence into
# tests/unit/preprocessing/ (see the handover items in the module docstring).
enforce_offline_pins()

#: Set only after the import-time gate has actually run. Asserted by
#: ``test_offline_pins_ran_at_import_time`` so that deleting the call above
#: is caught rather than silently returning this file to "15 skipped".
_IMPORT_TIME_GATE_RAN = True
