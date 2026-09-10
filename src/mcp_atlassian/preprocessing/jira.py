"""Jira-specific text preprocessing module."""

import logging
import re
from collections.abc import Callable
from typing import Any

from .base import BasePreprocessor, _extract_blocks, _restore_blocks

logger = logging.getLogger("mcp-atlassian")

_ISSUE_KEY_PATTERN = r"[A-Z][A-Z0-9_]+-\d+(?:-\d+)*"

_UNESCAPED_BRACE_RE = re.compile(r"(?<!\\)\{")
_ESCAPED_BRACE_RE = re.compile(r"\\([{}])")
_ESCAPED_OPEN_BRACE_RE = re.compile(r"\\(\{)")

# Only a real URL scheme may have its brackets unwrapped.  A bare
# ``word:`` prefix is NOT a scheme: ``[Note: check this]``, ``[TODO: fix]``,
# ``[CHSTC-1101: summary]`` (``-`` is in the class), ``[ISO: 8601]`` and
# ``[C:\temp\f.txt]`` all satisfy ``[A-Za-z][A-Za-z0-9+.-]*:`` yet render as
# ``<span class="error">`` -- byte for byte the same shape as ``[WIP]``,
# which this module deliberately preserves.  Unwrapping them silently
# rewrites the author's text, and it was also internally inconsistent:
# ``[12:30]`` kept its brackets only because it starts with a digit.
# ``//`` is therefore required, plus a small allowlist for the
# schemeless-authority schemes Jira autolinks, and a space in the target
# disqualifies it.
_JIRA_BARE_LINK_RE = re.compile(
    r"(?<![!\w\]])\["
    r"((?:[A-Za-z][A-Za-z0-9+.-]*://|mailto:|file:|tel:)[^\]\s]+)"
    r"\](?!\()"
)

# A Jira ``{code}``/``{noformat}`` block's argument list is ``:language``,
# ``:language|key=value``, ``|key=value`` or ``:key=value``; the macro name
# is case-insensitive and a language may contain ``+``, ``#``, ``-`` and
# ``.`` (``c++``, ``c#`` and ``objective-c`` are all rendered with syntax
# highlighting by Jira Server/DC).  The old ``(?::([a-z]+))?`` accepted only
# a bare lowercase language, so every parameterised or non-lowercase block
# was left RAW in the "Markdown" output and the write path's brace escape
# then flattened it to literal paragraph text -- panel, header and syntax
# highlighting all gone (measured on rendered HTML, CHSTC-1101).
#
# ``(?<!\{)`` is a SECOND, SEPARATE lookbehind -- not a widening of the
# backslash guard.  A ``{{...}}`` monospace span ends in ``}}``, so its
# payload plus that terminator is byte for byte a block-macro delimiter:
# ``a {{CODE}} b`` contains ``{CODE}`` starting at the span's second brace.
# Making the macro name case-insensitive (needed for ``{Code:java}``)
# therefore made ``{{CODE}}``, ``{{Code}}`` and ``{{NOFORMAT}}`` newly
# collide with it.  Measured on
# ``a {{CODE}} b\n{code:java}\nint x;\n{code}``: the read produced
# ``a {```\n} b\n{code:java}\nint x;\n``` `` -- the REAL code block
# swallowed into a bogus fence and the monospace opener orphaned -- which
# broke the write -> read -> write fixed point and silently lower-cased the
# author's content on the way through (rendered: ``<p>the <tt>code</tt>
# constant</p>`` where the author wrote ``CODE``).  Refusing an
# opener/closer whose brace is the tail of ``{{`` leaves the span to the
# monospace extractor, which runs after these two.
#
# Do NOT collapse the two lookbehinds into one negated class
# ``(?<![\\{])``: that silently drops the backslash guard, so a
# hand-authored literal ``\{code}c\{code}`` is parsed as a macro again and
# its delimiters are permanently deleted.
_JIRA_CODE_BLOCK_RE = (
    r"(?<!\\)(?<!\{)\{(?i:code)([:|][^}\n]*)?\}"
    r"([\s\S]*?)(?<!\\)(?<!\{)\{(?i:code)\}"
)
_JIRA_NOFORMAT_BLOCK_RE = (
    r"(?<!\\)(?<!\{)\{(?i:noformat)([:|][^}\n]*)?\}"
    r"([\s\S]*?)(?<!\\)(?<!\{)\{(?i:noformat)\}"
)

# Markdown fence info string used to carry a Jira ``{noformat}`` block
# through the Markdown representation so the writer can rebuild it rather
# than downgrading it to a ``{code}`` panel.  Jira has no ``noformat``
# language, so the token cannot collide with a real one.
_NOFORMAT_INFO = "noformat"

# Which ``{name}`` runs the write-path brace escape must leave alone.
#
# MEASURED, not assumed (CHSTC-1101, raw wiki posted over REST,
# ``expand=renderedBody``, 2026-09-10).  Thirty-eight candidate macro names
# were rendered inside a paragraph as ``AAA {name[:args]} BBB``.  Exactly
# seven produced real markup -- ``anchor``, ``code``, ``color``,
# ``loremipsum``, ``noformat``, ``panel``, ``quote``.  Every other name --
# ``toc``, ``status``, ``expand``, ``section``, ``column``, ``align``,
# ``children``, ``info``, ``note``, ``tip``, ``warning``, ``jira``,
# ``jiraissues``, ``include``, ``excerpt``, ``html``, ``div``, ``span``,
# ``sub``, ``sup``, ``center``, ``gallery``, ``thumbnail``, ``tasklist``,
# ``deck``, ``card``, ``table-plus``, ``chart``, ``contentbylabel``,
# ``cache``, ``cheese`` -- is NOT installed there and renders as a literal
# block that TEARS the enclosing paragraph, byte for byte the same damage
# as an unknown ``{b}``.  For those names the escape is therefore strictly
# BETTER than passing them through (escaped, each renders inline as literal
# text in one intact ``<p>``), so they are deliberately NOT exempted and
# the escape stays the default for everything unlisted.
#
# ``loremipsum`` is installed but omitted on purpose: it is a demo macro
# that expands to a wall of filler text, nobody stores it in a field they
# then edit, and escaping it is the safer of two irrelevant outcomes.

# Exempt unconditionally -- measured to render INLINE, so a lone
# occurrence costs nothing: ``see {anchor:top} here`` renders
# ``see <a name="top"></a> here`` inside one ``<p>``.  Escaped it renders
# the literal text ``{anchor:top}``, so the anchor element -- and every
# ``#top`` deep link pointing at it -- is permanently gone on the first
# read -> write cycle.  That measurement is what makes this exemption
# necessary rather than merely tidy.
_WIKI_INLINE_MACROS = frozenset({"anchor"})

# Exempt only as a COMPLETE opener/closer PAIR.  Paired, all five render as
# real markup a read -> write cycle must preserve (``<font color>``,
# ``<blockquote>``, the code/preformatted/panel ``<div>``s).  A LONE
# occurrence is measured destructive, so prose that merely mentions one
# keeps its escape:
#
#     use {code} for code      -> opens a code panel that SWALLOWS the
#                                 whole rest of the document
#     use {noformat} in prose  -> tears the paragraph into three fragments
#     use {panel} in prose     -> injects an empty <div class="panel">
#     use {quote} in prose     -> injects an empty <blockquote>
#     use {color:red} in prose -> injects an empty <font color="red">
#
# while the escaped form of each renders as one intact ``<p>`` holding the
# literal text.  Pairing is what keeps this an evidence-based rule instead
# of the "skip braces that look like a macro" guess that would re-open
# defect 1: an unmatched delimiter is prose, and prose gets escaped.
_WIKI_PAIRED_MACROS = frozenset({"code", "color", "noformat", "panel", "quote"})

# A macro invocation: ``{name}``, ``{name:args}`` or ``{name|args}``.  The
# ``(?<!\{)`` guard means a token that is only the tail of a ``{{...}}``
# monospace span (``{CODE}`` inside ``{{CODE}}``) is not counted as a macro
# -- the same collision :data:`_JIRA_CODE_BLOCK_RE` guards against, and
# without it a monospace payload could pair with a real macro's closer.
_MACRO_TOKEN_RE = re.compile(
    r"(?<!\\)(?<!\{)\{([A-Za-z][A-Za-z0-9_-]*)(?:[:|][^}\n]*)?\}"
)

# A markdown IMAGE target that is an absolute URL.  Only these get their
# braces percent-encoded; see :func:`_encode_braces_in_image_urls`.
_MD_IMAGE_URL_TARGET_RE = re.compile(
    r"(!\[[^\]\n]*\]\()([A-Za-z][A-Za-z0-9+.-]*://[^)\n]+)(\))"
)


def _macro_brace_offsets(text: str) -> set[int]:
    """Offsets of ``{`` that open a Jira macro the brace escape must keep.

    See :data:`_WIKI_INLINE_MACROS` and :data:`_WIKI_PAIRED_MACROS` for the
    measured rationale.  Occurrences of a paired macro are matched up two
    at a time in document order; an odd trailing occurrence is an unmatched
    delimiter, i.e. prose, and is left for the escape.

    Args:
        text: The text about to be brace-escaped.

    Returns:
        The set of ``{`` offsets in ``text`` to leave unescaped.
    """
    exempt: set[int] = set()
    paired: dict[str, list[int]] = {}
    for match in _MACRO_TOKEN_RE.finditer(text):
        name = match.group(1).lower()
        if name in _WIKI_INLINE_MACROS:
            exempt.add(match.start())
        elif name in _WIKI_PAIRED_MACROS:
            paired.setdefault(name, []).append(match.start())
    for offsets in paired.values():
        exempt.update(offsets[: len(offsets) - len(offsets) % 2])
    return exempt


def _encode_braces_in_image_urls(text: str) -> str:
    """Percent-encode braces in an absolute-URL markdown image target.

    Jira strips the ``\\{`` escape from a LINK target but keeps it inside an
    IMAGE target, so an escaped image URL renders an ``<img>`` whose ``src``
    is wrong by one character (measured:
    ``src="https://example.com/a/\\{v}.png"``).  Exempting image targets
    from the escape was measured far worse -- unescaped,
    ``!https://example.com/a/{v}.png|alt=img!`` renders
    ``<p>!https://example.com/a/</p>\\n{v}\\n<p>.png|alt=img!</p>``: the
    paragraph torn into three, ``{v}`` promoted to a macro block and no
    ``<img>`` at all.  Percent-encoding is the third option and needs no
    exemption: ``%7B``/``%7D`` cannot re-open the macro hole, and
    ``src="https://example.com/a/%7Bv%7D.png"`` is a correct, resolving URL
    for ``.../a/{v}.png`` (measured on rendered HTML).  ``{`` and ``}`` are
    excluded from every RFC 3986 URI production, so this is canonicalisation
    rather than an invented rewrite.

    Deliberately limited to targets carrying a ``scheme://``.  An ATTACHMENT
    target is a filename that Jira looks up literally, so percent-encoding
    it would stop the lookup finding an attachment genuinely named
    ``scr{1}.png``; those keep the documented backslash escape.  The escape
    is idempotent here because ``%7B`` contains no brace, and the Markdown
    round trip is unaffected either way.

    Args:
        text: Markdown that may contain image links.

    Returns:
        The text with braces in absolute image URLs percent-encoded.
    """

    def encode(match: re.Match[str]) -> str:
        target = re.sub(r"\\?\{", "%7B", match.group(2))
        target = re.sub(r"\\?\}", "%7D", target)
        return match.group(1) + target + match.group(3)

    return _MD_IMAGE_URL_TARGET_RE.sub(encode, text)


def _escape_wiki_braces(text: str, *, keep_macros: bool = False) -> str:
    """Escape ``{`` so the Jira wiki renderer emits it literally.

    An unescaped ``{...}`` pair on one line is parsed as macro syntax and
    breaks the enclosing block: a paragraph is torn into fragments and a
    list item swallows following content (measured on Jira Server/DC).
    Only the OPENING brace is escaped -- a lone ``}`` renders literally,
    and escaping the closing brace as well collides with the ``}}``
    monospace terminator.  The ``(?<!\\)`` lookbehind makes this
    idempotent: double escaping (``\\\\{``) is measured to tear the
    paragraph AND inject a forced newline, so it is a correctness bug.

    With ``keep_macros=True`` the six macro names Jira Server/DC actually
    installs are exempted (see :func:`_macro_brace_offsets`), which is what
    lets a real macro survive a read -> write cycle and lets raw wiki
    handed to the writer keep its blocks.  The document-level call site
    passes it; the ``{{monospace}}`` call site deliberately does NOT --
    content the author fenced as code is unambiguously content, so
    ```` `{code}x{code}` ```` must stay a literal monospace span rather
    than become a real code panel nested inside one.

    Accepted trade-offs, every one confirmed on rendered HTML (Jira
    Server/DC, CHSTC-1101/1102).  They are recorded here rather than left
    implicit because each is a render difference a reader could call a
    regression, and none of them justifies a per-context exemption: an
    exemption has to guess whether a ``{...}`` the user typed is content or
    markup, which is the ambiguity this escape exists to remove.

    * Bare (unbracketed) URL containing a brace -- cosmetically worse.
      ``go https://example.com/a/{env}/b now`` now renders a visible
      backslash inside the anchor TEXT (measured:
      ``>https://example.com/a/\\</a>{env}/b now``).  A bare URL is prose
      to this converter, so it is left to Jira's own autolinker, which
      ends the link at the backslash and prints it.  Kept anyway: the
      href is truncated at the brace in BOTH states, and unescaped the
      paragraph was also torn into three fragments with ``{env}``
      promoted to a macro block.
      An author who needs a brace in a URL should write a Markdown link
      or autolink, which this converter turns into explicitly bracketed
      wiki markup that renders the escape perfectly -- measured, both
      ``[https://example.com/a/\\{env}/b]`` and
      ``[cfg|https://example.com/a/\\{env}/b]`` render
      ``href="https://example.com/a/{env}/b"`` with no backslash
      anywhere, while that same bracketed form UNESCAPED renders
      ``<p>go [https://example.com/a/</p>`` plus a ``{env}`` macro block
      -- so the escape is required there too, and only the bare form
      pays for it.
    * Brace in a markdown IMAGE target -- an absolute URL is
      percent-encoded before this escape runs, so it renders a clean
      ``src`` (see :func:`_encode_braces_in_image_urls`); an ATTACHMENT
      target still carries the escape into ``imagetext`` because a
      filename is looked up literally.  Exempting image targets from the
      escape instead was measured on rendered HTML and is far worse than
      either: unescaped, ``!https://example.com/a/{v}.png|alt=img!``
      renders ``<p>!https://example.com/a/</p>\\n{v}\\n<p>.png|alt=img!
      </p>`` -- the paragraph torn into three fragments, ``{v}`` promoted
      to a macro block and NO image element at all.
    * Brace in HEADING text -- strictly worse, and cheap.  Jira's own
      generated anchor id degrades from ``<a name="Section">`` to
      ``<a name="Section%5C">``, so an existing ``#Section`` deep link
      breaks.  The visible heading text is correct either way (the
      pre-fix render of ``h2. Section {A}`` measured fully correct), so
      for headings alone the escape buys nothing.
    * Prose the user hand-wrote as an UNINSTALLED macro no longer renders
      as one -- but on the reference instance it never did.  Every name
      outside the exempt set renders as a literal, paragraph-tearing block
      when unescaped, so the escape is the better render, not a loss:
      ``\\{status:colour=Green|title=Done}`` renders
      ``<p>{status:colour=Green|title=Done}</p>`` in one intact paragraph,
      where the unescaped form tears it into three.  Prose the user
      hand-wrote as an INSTALLED macro pair (``literal {color:red}x{color}
      typed``) IS now treated as markup and renders
      ``<font color="red">x</font>``, which is what it did before this
      change and what an author round-tripping a real description needs.
      An author who wants such a pair literal has an escape hatch this
      function honours: writing ``\\{color:red}`` passes through untouched,
      and it is measured to render as literal text.  A caller who wants to
      post wiki markup wholesale has the tested global passthrough,
      ``DISABLE_JIRA_MARKUP_TRANSLATION=true``.
    * Stored fields now contain ``\\{``.  ``jira_to_markdown`` strips it
      (:func:`_unescape_wiki_braces` plus the terminal unescape pass), so
      any read routed through ``_clean_text`` is unaffected -- but the
      tool responses that build models straight from the REST payload
      (``jira_search``, ``jira_update_issue``, ``jira_create_issue``,
      ``jira_batch_create_issues``) surface the backslash verbatim.
      Feeding such a response back into this converter re-escapes the
      ``{{`` it emitted and loses the monospace span, so writer output
      must be read through ``clean_jira_text`` first:
      ``markdown_to_jira(clean_jira_text(x))`` is a fixed point, while
      ``markdown_to_jira(markdown_to_jira(x))`` never was -- it already
      mangled bold, headings, lists and tables before this change.

    Args:
        text: User text that must render literally in Jira wiki markup.
        keep_macros: Leave the braces of an installed Jira macro alone.

    Returns:
        The text with every unescaped opening brace backslash-escaped,
        except the macro braces ``keep_macros`` preserves.
    """
    if not keep_macros:
        return _UNESCAPED_BRACE_RE.sub(r"\\{", text)

    exempt = _macro_brace_offsets(text)
    if not exempt:
        return _UNESCAPED_BRACE_RE.sub(r"\\{", text)

    logger.debug(
        "jira markup: leaving %d macro brace(s) unescaped; input carried "
        "Jira wiki macro syntax",
        len(exempt),
    )
    # A replacement FUNCTION is required: the decision is per-offset, and
    # a function's return value is used verbatim (no template expansion),
    # so "\\{" is one backslash plus one brace, exactly as the template
    # r"\\{" produced above.
    return _UNESCAPED_BRACE_RE.sub(
        lambda match: match.group(0) if match.start() in exempt else "\\{",
        text,
    )


def _unescape_wiki_braces(text: str) -> str:
    """Strip wiki brace escapes from PROSE; Markdown has no such escape.

    Both ``\\{`` and ``\\}`` are stripped: the renderer treats either as an
    escape in running text, so a hand-authored ``\\{k\\}`` is a literal
    ``{k}`` and reporting the backslashes would surface wiki noise in a
    field documented as Markdown.

    Do NOT use this on code content -- see
    :func:`_unescape_code_braces` for why.

    Args:
        text: Jira wiki prose that may carry ``\\{`` / ``\\}`` escapes.

    Returns:
        The text with the brace escapes removed.
    """
    return _ESCAPED_BRACE_RE.sub(r"\1", text)


def _unescape_code_braces(text: str) -> str:
    """Strip only the brace escape this module's writer can author.

    Inside ``{{monospace}}`` a backslash is ordinarily CONTENT (a LaTeX
    ``\\{x\\}``, a shell ``echo \\{a,b\\}``), so the reader must remove
    exactly the escapes the writer emits and nothing else.
    :func:`_escape_wiki_braces` only ever emits ``\\{``; stripping ``\\}``
    as well deleted a user's own backslash and broke the
    write -> read -> write fixed point:

    * ``latex `\\{x\\}` inline`` wrote ``latex {{\\{x\\}}} inline``, read
      back as ``latex `{x}` inline`` -- both user backslashes gone -- and
      re-wrote as ``latex {{\\{x}}} inline``, which differs from the first
      write.  Hand-authored ``{{\\{x\\}}}`` drifted the same way.

    A ``{code}`` block keeps both escapes verbatim because the writer never
    escapes inside one, so the rule is uniform: strip exactly what the
    writer could have written in that context.

    Args:
        text: Monospace span content that may carry a ``\\{`` escape.

    Returns:
        The content with writer-authored opening-brace escapes removed.
    """
    return _ESCAPED_OPEN_BRACE_RE.sub(r"\1", text)


def _jira_macro_args_to_fence_info(kind: str, args: str | None) -> str:
    """Encode a ``{code}``/``{noformat}`` argument list as a fence info string.

    The Markdown fence info string is the only place a round trip can carry
    the macro's parameters, and dropping them is visible: ``{code:java|
    title=Example}`` renders a panel with a bold ``Example`` header.  A
    leading ``:`` is dropped so the ordinary case stays idiomatic Markdown
    (``{code:java}`` -> ``java``); a leading ``|`` is kept so the writer can
    tell ``{code|title=T}`` from ``{code:title=T}``.

    Args:
        kind: ``"code"`` or ``"noformat"``.
        args: The raw argument text including its leading ``:`` or ``|``.

    Returns:
        The fence info string (possibly empty, for a bare ``{code}``).
    """
    args = args or ""
    if kind == "noformat":
        # ``noformat`` has no language slot, so the whole argument list is
        # parameters and the separator is preserved verbatim.
        return _NOFORMAT_INFO + args
    if args.startswith(":"):
        args = args[1:]
    return args


def _fence_info_to_jira_macro(
    info: str, normalize_language: "Callable[[str | None], str | None]"
) -> str:
    """Decode a fence info string back into a Jira macro opener.

    Inverse of :func:`_jira_macro_args_to_fence_info`.

    Args:
        info: The fence info string.
        normalize_language: Maps a Markdown language to a Jira language, or
            to ``None`` when Jira has no equivalent.

    Returns:
        The macro opener, e.g. ``{code:java|title=X}`` or ``{noformat}``.
    """
    if info == _NOFORMAT_INFO or (
        info.startswith(_NOFORMAT_INFO)
        and info[len(_NOFORMAT_INFO) : len(_NOFORMAT_INFO) + 1] in (":", "|")
    ):
        return "{noformat" + info[len(_NOFORMAT_INFO) :] + "}"

    head, sep, tail = info.partition("|")
    tail = sep + tail
    if not head:
        # ``{code|key=value}``: no language slot was used.
        return "{code" + tail + "}"
    if "=" in head:
        # A parameter in the language slot (``{code:borderStyle=solid}``);
        # pass it through verbatim rather than guessing it is a language.
        return "{code:" + head + tail + "}"
    jira_lang = normalize_language(head)
    return "{code" + (":" + jira_lang if jira_lang else "") + tail + "}"


def _sub_wiki_span(text: str, delim: str, tag: str) -> str:
    """Replace a flat ``<delim>content<delim>`` wiki span with an HTML tag.

    Encodes the Jira renderer's measured rule: the delimiter is refused
    intraword and refused when doubled, and the content is single-line,
    non-empty and not whitespace-padded.  A blanket ``X(...)X`` pattern
    instead lets every delimiter act as an opener for the next one, which
    is what turned ``1+2+3 and a+b and C++`` into interleaved ``<ins>``
    runs across unrelated words.

    Args:
        text: Jira wiki text to scan.
        delim: The single-character wiki delimiter (``+``, ``^``, ``~``, ``-``).
        tag: The HTML tag name to emit (``ins``, ``sup``, ``sub``, ``del``).

    Returns:
        The text with well-formed spans replaced by ``<tag>`` elements.
    """
    d = re.escape(delim)
    pattern = rf"(?<![{d}\w]){d}(?![\s{d}])([^{d}\n]*[^\s{d}]){d}(?![{d}\w])"
    return re.sub(pattern, rf"<{tag}>\1</{tag}>", text)


def _convert_panel(params: str | None, content: str) -> str:
    """Convert a Jira {panel} block to markdown."""
    title = ""
    if params:
        title_match = re.search(r"title=([^|}]+)", params)
        if title_match:
            title = title_match.group(1).strip()
    content = content.strip()
    if title:
        return f"\n**{title}**\n{content}\n"
    return f"\n{content}\n"


class JiraPreprocessor(BasePreprocessor):
    """Handles text preprocessing for Jira content."""

    # Step 1: Valid JIRA languages (official list)
    # Source: https://jira.atlassian.com/browse/JRASERVER-21067 (JIRA 7.5.0+)
    # and JIRA v9.12.12 release notes
    # Official documentation: https://jira.atlassian.com/secure/WikiRendererHelpAction.jspa
    VALID_JIRA_LANGUAGES = {
        # Core languages from JIRA 7.5.0+
        "actionscript",
        "actionscript3",
        "ada",
        "applescript",
        "bash",
        "sh",  # alias for bash
        "c",
        "c#",
        "csharp",  # alias for c#
        "cs",  # alias for c#
        "c++",
        "cpp",  # alias for c++
        "css",
        "sass",  # CSS preprocessor
        "less",  # CSS preprocessor
        "coldfusion",
        "delphi",
        "diff",
        "patch",  # alias for diff
        "erlang",
        "erl",  # alias for erlang
        "go",
        "groovy",
        "haskell",
        "html",
        "xml",
        "java",
        "javafx",
        "javascript",
        "js",  # alias for javascript
        "json",
        "lua",
        "nyan",
        "objc",
        "objective-c",  # alias for objc
        "perl",
        "php",
        "powershell",
        "ps1",  # alias for powershell
        "python",
        "py",  # alias for python
        "r",
        "rainbow",
        "ruby",
        "rb",  # alias for ruby
        "scala",
        "sql",
        "swift",
        "visualbasic",
        "vb",  # alias for visualbasic
        "yaml",
        "yml",  # alias for yaml
        "none",  # plain text, no highlighting
    }

    # Step 2: Mapping for unsupported languages to closest valid JIRA alternative
    # Only map to actual JIRA languages; unmapped languages will return None → {code}
    LANGUAGE_MAPPING = {
        # Dockerfile → bash (similar shell syntax)
        "dockerfile": "bash",
        "docker": "bash",
        # TypeScript → javascript
        "typescript": "javascript",
        "ts": "javascript",
        "tsx": "javascript",
        # JSX/React → javascript
        "jsx": "javascript",
        # Kotlin → java (JVM-based language)
        "kotlin": "java",
        "kt": "java",
        # Build files → bash
        "makefile": "bash",
        "make": "bash",
        "cmake": "bash",
    }

    def __init__(
        self, base_url: str = "", disable_translation: bool = False, **kwargs: Any
    ) -> None:
        """
        Initialize the Jira text preprocessor.

        Args:
            base_url: Base URL for Jira API
            disable_translation: If True, disable markup translation between formats
            **kwargs: Additional arguments for the base class
        """
        super().__init__(base_url=base_url, **kwargs)
        self.disable_translation = disable_translation

    def clean_jira_text(self, text: str) -> str:
        """
        Clean Jira text content by:
        1. Processing user mentions and links
        2. Converting Jira markup to markdown (if translation enabled)
        3. Converting HTML/wiki markup to markdown (if translation enabled)
        """
        if not text:
            return ""

        # Process user mentions
        mention_pattern = r"\[~accountid:(.*?)\]"
        text = self._process_mentions(text, mention_pattern)

        # Process Jira smart links
        text = self._process_smart_links(text)

        # Convert markup only if translation is enabled
        if not self.disable_translation:
            # First convert any Jira markup to Markdown
            text = self.jira_to_markdown(text)

            # Then convert any remaining HTML to markdown
            text = self._convert_html_to_markdown(text)

        return text.strip()

    def _process_mentions(self, text: str, pattern: str) -> str:
        """
        Process user mentions in text.

        Args:
            text: The text containing mentions
            pattern: Regular expression pattern to match mentions

        Returns:
            Text with mentions replaced with display names
        """
        mentions = re.findall(pattern, text)
        for account_id in mentions:
            try:
                # Note: This is a placeholder - actual user fetching should be injected
                display_name = f"User:{account_id}"
                text = text.replace(f"[~accountid:{account_id}]", display_name)
            except Exception as e:
                logger.error(f"Error processing mention for {account_id}: {str(e)}")
        return text

    def _process_smart_links(self, text: str) -> str:
        """Process Jira/Confluence smart links."""
        # Pattern matches: [text|url|smart-link]
        link_pattern = r"\[(.*?)\|(.*?)\|smart-link\]"
        matches = re.finditer(link_pattern, text)

        for match in matches:
            full_match = match.group(0)
            link_text = match.group(1)
            link_url = match.group(2)

            # Extract issue key if it's a Jira issue link
            issue_key_match = re.search(
                rf"browse/({_ISSUE_KEY_PATTERN})(?=$|[/?#])", link_url
            )
            # Check if it's a Confluence wiki link
            confluence_match = re.search(
                r"wiki/spaces/.+?/pages/\d+/(.+?)(?:\?|$)", link_url
            )

            if issue_key_match:
                issue_key = issue_key_match.group(1)
                clean_url = f"{self.base_url}/browse/{issue_key}"
                text = text.replace(full_match, f"[{issue_key}]({clean_url})")
            elif confluence_match:
                url_title = confluence_match.group(1)
                readable_title = url_title.replace("+", " ")
                readable_title = re.sub(
                    rf"^{_ISSUE_KEY_PATTERN}\s+", "", readable_title
                )
                text = text.replace(full_match, f"[{readable_title}]({link_url})")
            else:
                clean_url = link_url.split("?")[0]
                text = text.replace(full_match, f"[{link_text}]({clean_url})")

        return text

    def jira_to_markdown(self, input_text: str) -> str:
        """
        Convert Jira markup to Markdown format.

        Args:
            input_text: Text in Jira markup format

        Returns:
            Text in Markdown format (or original text if translation disabled)
        """
        if not input_text:
            return ""

        if self.disable_translation:
            return input_text

        output = input_text

        # Protect code/noformat/inline-code blocks from downstream
        # transformations by replacing them with placeholders.
        #
        # Trade-off: when {quote} wraps a {code} block, the code
        # content is extracted *before* the {quote} handler runs.
        # The {quote} handler prefixes each remaining line with
        # "> " but cannot reach inside the already-extracted block.
        # After restoration the opening fence line may carry "> "
        # while inner code lines do not, breaking blockquote
        # continuity.  This is intentional: protecting code content
        # from markup corruption is more important than preserving
        # blockquote indentation around code fences.
        code_blocks: list[str] = []
        inline_codes: list[str] = []

        def _jira_block_to_md(kind: str) -> "Callable[[re.Match[str]], str]":
            def convert(match: re.Match[str]) -> str:
                info = _jira_macro_args_to_fence_info(kind, match.group(1))
                # A wiki block's content conventionally opens and closes
                # with a newline, and the fence template adds its own.  Not
                # collapsing them made the blank lines GROW by one on every
                # read/write cycle (measured: three blank lines inside
                # <pre> after two cycles), because the writer copies the
                # fence body verbatim.  Collapsing bounds the drift at the
                # one pre-existing "no newline after {code:lang}" quirk.
                content = match.group(2).strip("\n")
                return f"```{info}\n{content}\n```"

            return convert

        output = _extract_blocks(
            output,
            _JIRA_CODE_BLOCK_RE,
            _jira_block_to_md("code"),
            code_blocks,
            "CODEBLOCK",
            flags=re.MULTILINE,
        )
        output = _extract_blocks(
            output,
            _JIRA_NOFORMAT_BLOCK_RE,
            _jira_block_to_md("noformat"),
            code_blocks,
            "CODEBLOCK",
        )
        output = _extract_blocks(
            output,
            # Newline-bounded and LAZY.  The bound stops one span's
            # opener pairing with a later line's terminator; the laziness
            # stops two adjacent spans merging into one.  Consequence,
            # recorded because it is a behaviour change: a genuinely
            # multi-line '{{...}}' span is no longer converted, so
            # '{{multi\nline}} mono' is left as raw wiki on read and the
            # writer then escapes it to '\{\{multi\nline}} mono'.  Both
            # forms render identically ('<p>{{multi<br/>line}} mono</p>',
            # measured), so the stored bytes drift but nothing visible
            # changes.
            r"(?<!\\)\{\{([^\n]+?)\}\}(?!\})",
            lambda m: f"`{_unescape_code_braces(m.group(1))}`",
            inline_codes,
            "INLINECODE",
        )

        # Block quotes
        output = re.sub(r"^bq\.(.*?)$", r"> \1\n", output, flags=re.MULTILINE)

        # Text formatting (bold, italic). Delimiters preceded by a backslash
        # are wiki escapes (e.g. the intraword `\_` this module's
        # markdown_to_jira writes), not markup, so they must neither open nor
        # close a span.
        output = re.sub(
            r"(?<!\\)([*_])(.*?)(?<!\\)\1",
            lambda match: (
                ("**" if match.group(1) == "*" else "*")
                + match.group(2)
                + ("**" if match.group(1) == "*" else "*")
            ),
            output,
        )

        # Multi-level numbered list
        output = re.sub(
            r"^((?:#|-|\+|\*)+) (.*)$",
            lambda match: self._convert_jira_list_to_markdown(match),
            output,
            flags=re.MULTILINE,
        )

        # Headers
        output = re.sub(
            r"^h([0-6])\.(.*)$",
            lambda match: "#" * int(match.group(1)) + match.group(2),
            output,
            flags=re.MULTILINE,
        )

        # Citation (non-overlapping alternation to avoid catastrophic backtracking)
        output = re.sub(
            r"(?<![?\w])\?\?([^?]+(?:\?[^?]+)*)\?\?(?![?\w])",
            r"<cite>\1</cite>",
            output,
        )

        # Inserted text
        output = _sub_wiki_span(output, "+", "ins")

        # Superscript
        output = _sub_wiki_span(output, "^", "sup")

        # Subscript
        output = _sub_wiki_span(output, "~", "sub")

        # Strikethrough
        output = _sub_wiki_span(output, "-", "del")

        # Quote blocks
        output = re.sub(
            r"(?<!\\)\{quote\}([\s\S]*)(?<!\\)\{quote\}",
            lambda match: "\n".join(
                [f"> {line}" for line in match.group(1).split("\n")]
            ),
            output,
            flags=re.MULTILINE,
        )

        # Panel blocks - extract content, optionally show title as bold
        output = re.sub(
            r"(?<!\\)\{panel(?::([^}]*))?\}([\s\S]*?)(?<!\\)\{panel\}",
            lambda match: _convert_panel(match.group(1), match.group(2)),
            output,
            flags=re.MULTILINE,
        )

        # Images with alt text
        output = re.sub(
            r"!([^|\n\s]+)\|([^\n!]*)alt=([^\n!\,]+?)"
            r"(,([^\n!]*))?!",
            r"![\3](\1)",
            output,
        )

        # Images with other parameters (ignore them)
        output = re.sub(r"!([^|\n\s]+)\|([^\n!]*)!", r"![](\1)", output)

        # Images without parameters
        output = re.sub(r"!([^\n\s!]+)!", r"![](\1)", output)

        # Links
        output = re.sub(r"\[([^|]+)\|(.+?)\]", r"[\1](\2)", output)
        output = _JIRA_BARE_LINK_RE.sub(r"\1", output)

        # Colored text
        output = re.sub(
            r"(?<!\\)\{color:([^}]+)\}([\s\S]*?)(?<!\\)\{color\}",
            r'<span style="color:\1">\2</span>',
            output,
            flags=re.MULTILINE,
        )

        # Convert Jira table headers (||) to markdown table format
        lines = output.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]

            if "||" in line:
                # Replace Jira table headers
                lines[i] = lines[i].replace("||", "|")

                # Add a separator line for markdown tables
                header_cells = lines[i].count("|") - 1
                if header_cells > 0:
                    separator_line = "|" + "---|" * header_cells
                    lines.insert(i + 1, separator_line)
                    i += 1

            i += 1

        # Rejoin the lines
        output = "\n".join(lines)

        # Wiki `\{` is an escape, not content, and Markdown has no such
        # escape, so a faithful reader drops it.  This must run AFTER every
        # `{...}` macro pass (so no macro's own braces are confused) and
        # BEFORE the code restores: restored {code}/{noformat} content is
        # verbatim Jira content that the writer never escaped, so it keeps
        # its literal backslashes.  Inline-code content is unescaped in its
        # own extraction transform, not here.
        output = _unescape_wiki_braces(output)

        # Restore code/noformat blocks and inline code
        output = _restore_blocks(output, code_blocks, "CODEBLOCK")
        output = _restore_blocks(output, inline_codes, "INLINECODE")

        return output

    def _normalize_code_language(self, lang: str | None) -> str | None:
        """
        Normalize and map markdown code language to JIRA-supported language.

        Step 3: Default handling - unmapped languages return None for plain {code}

        Args:
            lang: Language identifier from markdown code block

        Returns:
            Valid JIRA language string, or None for plain {code} block
        """
        if not lang:
            return None

        lang_lower = lang.lower()

        # Step 1: Check if already valid JIRA language
        if lang_lower in self.VALID_JIRA_LANGUAGES:
            return lang_lower

        # Step 2: Check language mapping
        if lang_lower in self.LANGUAGE_MAPPING:
            return self.LANGUAGE_MAPPING[lang_lower]

        # Step 3: Default - unmapped language returns None for plain {code}
        return None

    def markdown_to_jira(self, input_text: str) -> str:
        """
        Convert Markdown syntax to Jira markup syntax.

        ``input_text`` SHOULD be Markdown, and the supported composition is
        ``markdown_to_jira(clean_jira_text(stored))``, which IS a fixed
        point.  Handing it raw Jira wiki markup instead is a caller error
        that is easy to make -- ``jira_search``, ``jira_update_issue``,
        ``jira_create_issue`` and ``jira_batch_create_issues`` build their
        models straight from the REST payload, so they return RAW wiki for
        the same ``description`` field ``jira_get_issue`` returns as
        Markdown.  Routing those four responses through ``_clean_text`` is
        the real fix; it is a response-shape change across four tools and
        does not belong in this module.

        Because that error destroyed rendered markup, the brace escape is
        macro-aware: a COMPLETE pair of an installed macro, and every
        ``{anchor:...}``, passes through untouched, so a stored description
        fed straight back in keeps its blocks.  MEASURED, patched::

            {code:java}\nint x = 1;\n{code} -> unchanged, real code panel
            {quote}q{quote}                 -> unchanged, <blockquote>
            {color:red}r{color}             -> unchanged, <font color>
            {panel:title=T}b{panel}         -> unchanged, panel + header
            {noformat}n{noformat}           -> unchanged, <pre> panel
            {anchor:top}                    -> unchanged, <a name="top">
            {{mono}}                        -> \\{\\{mono}}  (not a macro)

        What is still NOT preserved, and why each is correct:

        * A macro name Jira does not install -- ``{toc}``, ``{status}``,
          ``{expand}``, ``{section}``/``{column}``, ``{align}``,
          ``{children}``, ``{info}``/``{note}``/``{tip}``/``{warning}``,
          ``{jira}``, any uninstalled plugin macro.  All 31 probed render
          UNESCAPED as a literal block that TEARS the enclosing paragraph,
          so escaping them is the better render, not a loss:
          ``\\{toc:maxLevel=2}`` renders ``<p>see {toc:maxLevel=2} here</p>``
          in one intact paragraph.  See :data:`_WIKI_PAIRED_MACROS` for the
          full measured list.
        * An UNMATCHED delimiter of an installed macro, because a lone one
          is measured destructive (``use {code} for code`` opens a code
          panel that swallows the rest of the document).  Prose that merely
          mentions a macro name keeps its escape.
        * ``{{monospace}}``, which is not a macro; and anything inside
          ``{{...}}`` or a fence, where content is unambiguously content.

        An author who wants an installed macro pair to stay literal writes
        ``\\{color:red}``: this function honours an existing escape and the
        renderer prints it verbatim.  A caller who wants to post wiki markup
        wholesale has ``DISABLE_JIRA_MARKUP_TRANSLATION=true``.

        Args:
            input_text: Text in Markdown format

        Returns:
            Text in Jira markup format (or original text if translation disabled)
        """
        if not input_text:
            return ""

        if self.disable_translation:
            return input_text

        code_blocks: list[str] = []
        inline_codes: list[str] = []

        def _md_code_to_jira(match: re.Match[str]) -> str:
            info = match.group(1) or ""
            content = match.group(2)
            opener = _fence_info_to_jira_macro(info, self._normalize_code_language)
            closer = "{noformat}" if opener.startswith("{noformat") else "{code}"
            return opener + content + closer

        def _md_inline_to_jira(
            match: re.Match[str],
        ) -> str:
            return "{{" + _escape_wiki_braces(match.group(1)) + "}}"

        # Extract code blocks and inline code before
        # any other transformations.
        # The info string is NOT ``\w*``: ``c++``, ``c#`` and
        # ``objective-c`` are all Jira-supported languages, and a fence the
        # pattern refuses is not extracted at all -- its content then
        # reaches the brace escape below and the leftover backtick pair is
        # re-captured as INLINE code, so ```` ```c#\nx = {1};\n``` ````
        # became ``` ``{{c#\nx = \{1};\n}}`` ``` and gained two more
        # escapes on every further cycle.
        output = _extract_blocks(
            input_text,
            r"```([^\n`]*)\n([\s\S]+?)```",
            _md_code_to_jira,
            code_blocks,
            "CODEBLOCK",
        )
        output = _extract_blocks(
            output,
            r"`([^`]+)`",
            _md_inline_to_jira,
            inline_codes,
            "INLINECODE",
        )

        # Escape braces in user text so Jira does not parse `{...}` as a
        # macro.  This exact position is the only correct one: no
        # converter-emitted brace exists in `output` yet ({color:...} is
        # emitted further down and {code}/{{...}} only reappear at the final
        # restores), and markdown LINK targets are still raw here -- a brace
        # inside a link target is a measured hazard and the MARKDOWNURL
        # protection below would hide it.
        #
        # Reachability of code content, stated precisely because the loose
        # form of this claim was wrong.  Content is unreachable exactly when
        # the extraction above matched it:
        #   * a closed ``` fence with any info string, and `inline code`
        #     -- placeholdered above, restored at the end;
        #   * NOT an UNTERMINATED ``` fence, NOT a ~~~ fence and NOT a
        #     4-space indented block.  None of those were ever converted to
        #     a {code} panel by this writer, so their fence markers stayed
        #     literal before this change too; what is new is that a brace in
        #     their body is escaped.  That renders as a literal brace (the
        #     escape is measured transparent in prose), so the body is
        #     readable rather than macro-parsed -- but it is still not a
        #     code block.  Supporting those shapes is a converter feature,
        #     not part of this fix.
        #
        # The escape emitted here is removed only by the read path
        # (`jira_to_markdown`), so writer output is not safe writer input --
        # see `_escape_wiki_braces` for that and the other measured,
        # accepted consequences.
        #
        # Image URLs are canonicalised first: percent-encoding a brace
        # inside an absolute image target renders a correct `src`, which
        # the backslash escape cannot, and it must happen before the
        # escape so there is no brace left for the escape to reach.
        output = _encode_braces_in_image_urls(output)
        # `keep_macros=True` only here.  This is the document-level call,
        # where a `{code:java}...{code}` pair is far more likely to be real
        # markup that leaked out of a raw-wiki read than an author's
        # literal text -- and where escaping it is measured total
        # destruction of a rendered block.
        output = _escape_wiki_braces(output, keep_macros=True)

        # Headers with = or - underlines. A setext heading needs actual text on
        # the line above the underline, so the lookahead keeps a blank line from
        # matching: `\n\n----\n` is a horizontal rule, which is already valid
        # Jira markup, not an empty `h2.` (issue #1587).
        output = re.sub(
            r"^(?=[^\n]*\S)(.*?)\n([=-])+$",
            lambda match: f"h{1 if match.group(2)[0] == '=' else 2}. {match.group(1)}",
            output,
            flags=re.MULTILINE,
        )

        # Headers with # prefix - require space after #
        # to distinguish from Jira lists (issue #786)
        output = re.sub(
            r"^([#]+) (.*)$",
            lambda match: f"h{len(match.group(1))}. " + match.group(2),
            output,
            flags=re.MULTILINE,
        )

        markdown_url_targets: list[str] = []

        def store_markdown_url_target(target: str) -> str:
            placeholder = f"\x00MARKDOWNURL{len(markdown_url_targets)}\x00"
            markdown_url_targets.append(target)
            return placeholder

        def protect_markdown_link_target(match: re.Match[str]) -> str:
            return (
                match.group(1)
                + store_markdown_url_target(match.group(2))
                + match.group(3)
            )

        def protect_markdown_autolink_target(match: re.Match[str]) -> str:
            return "<" + store_markdown_url_target(match.group(1)) + ">"

        output = re.sub(
            r"(!?\[[^\]\n]*\]\()([^)]+)(\))",
            protect_markdown_link_target,
            output,
        )
        output = re.sub(
            r"<((?:[A-Za-z][A-Za-z0-9+.-]*:[^>\s]+|[^<>\s@]+@[^<>\s@]+))>",
            protect_markdown_autolink_target,
            output,
        )

        # Bold and italic - skip lines starting with
        # asterisks+space (Jira list syntax, issue #786)
        def escape_intraword_underscore_runs(match: re.Match[str]) -> str:
            return r"\_" * len(match.group(0))

        def convert_bold_italic_line(line: str) -> str:
            # CommonMark treats underscores between two word characters as
            # literal text, not emphasis. The Jira wiki renderer does not:
            # it italicizes any ``_word_`` span, so identifiers such as
            # snake_case, customfield_10101 or foo_bar_baz would render with
            # spurious italics (and adjacent identifiers can pair into a
            # cross-token italic span). Escape intraword underscore runs as
            # ``\_`` so Jira renders them literally; genuine word-boundary
            # ``_emphasis_``/``__strong__`` is left intact for conversion below.
            line = re.sub(
                r"(?<=[^\W_])_+(?=[^\W_])",
                escape_intraword_underscore_runs,
                line,
            )
            if re.match(r"^[*_]+\s", line):
                return line
            return re.sub(
                r"([*_]+)(.*?)\1",
                lambda m: (
                    ("_" if len(m.group(1)) == 1 else "*")
                    + m.group(2)
                    + ("_" if len(m.group(1)) == 1 else "*")
                ),
                line,
            )

        lines = output.split("\n")
        output = "\n".join(convert_bold_italic_line(line) for line in lines)
        output = _restore_blocks(output, markdown_url_targets, "MARKDOWNURL")

        # Multi-level bulleted list
        def bulleted_list_fn(match: re.Match[str]) -> str:
            ident = len(match.group(1)) if match.group(1) else 0
            level = ident // 2 + 1
            return str("*" * level + " " + match.group(2))

        output = re.sub(
            r"^(\s+)?[-+*] (.*)$",
            bulleted_list_fn,
            output,
            flags=re.MULTILINE,
        )

        # Multi-level numbered list
        def numbered_list_fn(
            match: re.Match[str],
        ) -> str:
            ident = len(match.group(1)) if match.group(1) else 0
            level = ident // 2 + 1
            return str("#" * level + " " + match.group(2))

        output = re.sub(
            r"^(\s+)?\d+\. (.*)$",
            numbered_list_fn,
            output,
            flags=re.MULTILINE,
        )

        # HTML formatting tags to Jira markup.
        #
        # These five tags are the interchange format `jira_to_markdown`
        # emits for wiki spans Markdown cannot express, so the mapping is
        # load-bearing in both directions.  The unavoidable cost is that a
        # user who TYPES one of them into a Jira field now has it
        # re-interpreted as markup on write-back: stored
        # 'user typed <sub>x</sub> and <sup>y</sup>' comes back as
        # 'user typed ~x~ and ^y^' (real subscript/superscript), where
        # before it flattened to 'user typed x and y'.  Both are lossy --
        # markdownify used to DELETE the tags and keep only their text --
        # but the new shape changes the rendered appearance instead of just
        # dropping tags, which is worse for someone documenting HTML in a
        # ticket.  Accepted deliberately: making the two distinguishable
        # needs a private marker rather than real HTML tags for the
        # reader -> writer handoff, i.e. a change to the protected-tag list
        # in `preprocessing/base.py` as well as here.
        tag_map = {
            "cite": "??",
            "del": "-",
            "ins": "+",
            "sup": "^",
            "sub": "~",
        }

        for tag, replacement in tag_map.items():
            output = re.sub(
                rf"<{tag}>(.*?)<\/{tag}>",
                rf"{replacement}\1{replacement}",
                output,
            )

        # Colored text
        output = re.sub(
            r"<span style=\"color:([^\"]+)\">"
            r"([\s\S]*?)</span>",
            r"{color:\1}\2{color}",
            output,
            flags=re.MULTILINE,
        )

        # Strikethrough
        output = re.sub(r"~~(.*?)~~", r"-\1-", output)

        # Images without alt text
        output = re.sub(r"!\[\]\(([^)\n\s]+)\)", r"!\1!", output)

        # Images with alt text
        output = re.sub(
            r"!\[([^\]\n]+)\]\(([^)\n\s]+)\)",
            r"!\2|alt=\1!",
            output,
        )

        # Links
        output = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"[\1|\2]", output)
        output = re.sub(r"<([^>]+)>", r"[\1]", output)

        # Convert markdown tables to Jira table format
        # Issue #1343: parse full table blocks (header + separator + data rows),
        # strip whitespace from each cell, convert header to ||cell|| and data
        # rows to |cell|.
        lines = output.split("\n")
        i = 0
        while i < len(lines):
            # Look for a header row followed by a markdown separator line
            if (
                i < len(lines) - 1
                and re.match(r"^\|[-\s|:]+\|$", lines[i + 1])
                and re.match(r"^\|.*\|$", lines[i])
            ):
                # Header row
                header_cells = [cell.strip() for cell in lines[i].split("|")[1:-1]]
                lines[i] = "||" + "||".join(header_cells) + "||"
                lines.pop(i + 1)  # drop separator

                # Consume data rows while they look like table rows
                while i < len(lines) and re.match(
                    r"^\|.*\|$", lines[i + 1] if i + 1 < len(lines) else ""
                ):
                    data_cells = [
                        cell.strip() for cell in lines[i + 1].split("|")[1:-1]
                    ]
                    lines[i + 1] = "|" + "|".join(data_cells) + "|"
                    i += 1
            i += 1

        # Rejoin the lines
        output = "\n".join(lines)

        # Restore code blocks and inline code
        output = _restore_blocks(output, code_blocks, "CODEBLOCK")
        output = _restore_blocks(output, inline_codes, "INLINECODE")

        return output

    def _convert_jira_list_to_markdown(self, match: re.Match) -> str:
        """
        Helper method to convert Jira lists to Markdown format.

        Args:
            match: Regex match object containing the Jira list markup

        Returns:
            Markdown-formatted list item
        """
        jira_bullets = match.group(1)
        content = match.group(2)

        # Calculate indentation level based on number of symbols
        indent_level = len(jira_bullets) - 1
        indent = " " * (indent_level * 2)

        # Determine the marker based on the last character
        last_char = jira_bullets[-1]
        prefix = "1." if last_char == "#" else "-"

        return f"{indent}{prefix} {content}"
