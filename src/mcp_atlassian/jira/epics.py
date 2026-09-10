"""Module for Jira epic operations."""

import json
import logging
from typing import Any

from ..models.jira import JiraIssue
from .client import JiraClient
from .protocols import (
    FieldsOperationsProto,
    IssueOperationsProto,
    SearchOperationsProto,
    UsersOperationsProto,
)

logger = logging.getLogger("mcp-jira")

# Schema key Jira Software uses for the Epic Link custom field. It is the
# plugin key, so it is identical on Cloud, on Server/DC and in every
# localisation, unlike the field's display name.
EPIC_LINK_FIELD_SCHEMA = "com.pyxis.greenhopper.jira:gh-epic-link"
# Field ID Jira Cloud ships Epic Link under. Field discovery in
# ``FieldsMixin.get_field_ids_to_epic`` trusts this ID on its own, so the
# credibility check below has to accept it for the same reason.
CLOUD_EPIC_LINK_FIELD_ID = "customfield_10014"


class EpicsMixin(
    JiraClient,
    FieldsOperationsProto,
    IssueOperationsProto,
    SearchOperationsProto,
    UsersOperationsProto,
):
    """Mixin for Jira epic operations."""

    def _try_discover_fields_from_existing_epic(
        self, field_ids: dict[str, str]
    ) -> None:
        """
        Try to discover Epic fields from existing epics in the Jira instance.

        This is a fallback method used when standard field discovery doesn't find
        all the necessary Epic-related fields. It searches for an existing Epic and
        analyzes its field structure to identify Epic fields dynamically.

        Args:
            field_ids: Dictionary of field IDs to update with discovered fields
        """
        try:
            # If we already have both epic fields, no need to search
            if all(field in field_ids for field in ["epic_name", "epic_link"]):
                return

            # Find an Epic in the system
            epics_jql = "issuetype = Epic ORDER BY created DESC"
            results = self.jira.jql(epics_jql, fields="*all", limit=1)
            if not isinstance(results, dict):
                msg = f"Unexpected return value type from `jira.jql`: {type(results)}"
                logger.error(msg)
                raise TypeError(msg)

            # If no epics found, we can't use this method
            if not results or not results.get("issues"):
                logger.warning("No existing Epics found to analyze field structure")
                return

            # Get the most recent Epic
            epic = results["issues"][0]
            fields = epic.get("fields", {})
            logger.debug(f"Found existing Epic {epic.get('key')} to analyze")

            # Look for Epic Name and other Epic fields
            for field_id, value in fields.items():
                if not field_id.startswith("customfield_"):
                    continue

                # Analyze the field value to determine what it might be
                if isinstance(value, str) and field_id not in field_ids.values():
                    # Epic Name is typically a string value
                    if "epic_name" not in field_ids and 3 <= len(value) <= 255:
                        field_ids["epic_name"] = field_id
                        logger.debug(
                            f"Discovered possible Epic Name field from existing Epic: {field_id}"
                        )

                # Look for color-related values (typically a string like "green", "blue", etc.)
                elif isinstance(value, str) and value.lower() in [
                    "green",
                    "blue",
                    "red",
                    "yellow",
                    "orange",
                    "purple",
                ]:
                    if "epic_color" not in field_ids:
                        field_ids["epic_color"] = field_id
                        logger.debug(
                            f"Discovered possible Epic Color field from existing Epic: {field_id}"
                        )
                # Look for fields that might be used for Epic Link
                elif value is not None and "epic_link" not in field_ids:
                    # Epic Link typically references another issue key or ID
                    try:
                        # Sometimes the value itself is a string containing a key
                        if isinstance(value, str) and "-" in value and len(value) < 20:
                            field_ids["epic_link"] = field_id
                            logger.debug(
                                f"Discovered possible Epic Link field from existing Epic: {field_id}"
                            )
                    except Exception as e:
                        logger.debug(
                            f"Error analyzing potential Epic Link field: {str(e)}"
                        )

            logger.debug(
                f"Updated field IDs after analyzing existing Epic: {field_ids}"
            )

        except Exception as e:
            logger.error(f"Error discovering fields from existing Epic: {str(e)}")

    def prepare_epic_fields(
        self,
        fields: dict[str, Any],
        summary: str,
        kwargs: dict[str, Any],
        project_key: str | None = None,
    ) -> None:
        """
        Prepare epic-specific fields for issue creation.

        Args:
            fields: The fields dictionary to update
            summary: The issue summary that can be used as a default epic name
            kwargs: Additional fields from the user
            project_key: Optional project key for checking field requirements
        """
        try:
            # Get all field IDs
            field_ids = self.get_field_ids_to_epic()
            logger.info(f"Discovered Jira field IDs for Epic creation: {field_ids}")

            # Get required fields for Epic issue type if project_key provided
            required_fields = {}
            if project_key:
                try:
                    epic_type_id = self._find_epic_issue_type_id(project_key)
                    required_fields = self.get_required_fields(
                        epic_type_id or "Epic", project_key
                    )
                    logger.debug(
                        f"Required fields for Epic in project {project_key}: {list(required_fields.keys())}"
                    )
                except Exception as e:
                    logger.warning(f"Could not check field requirements: {e}")

            # Extract and handle epic_name
            epic_name_field = self._get_epic_name_field_id(field_ids)
            if epic_name_field:
                # Get epic name value
                epic_name = kwargs.pop(
                    "epic_name", kwargs.pop("epicName", summary)
                )  # Use summary as default if epic_name not provided

                # Check if this field is required
                if epic_name_field in required_fields:
                    # Add to fields for initial creation
                    fields[epic_name_field] = epic_name
                    logger.info(
                        f"Adding required Epic Name ({epic_name_field}: {epic_name}) to creation fields"
                    )
                else:
                    # Store for post-creation update as before
                    kwargs["__epic_name_field"] = epic_name_field
                    kwargs["__epic_name_value"] = epic_name
                    logger.info(
                        f"Storing optional Epic Name ({epic_name_field}: {epic_name}) for post-creation update"
                    )

            # Extract and handle epic_color
            epic_color_field = self._get_epic_color_field_id(field_ids)
            if epic_color_field:
                epic_color = (
                    kwargs.pop("epic_color", None)
                    or kwargs.pop("epicColor", None)
                    or kwargs.pop("epic_colour", None)
                    or "green"  # Default color
                )

                # Check if this field is required
                if epic_color_field in required_fields:
                    # Add to fields for initial creation
                    fields[epic_color_field] = epic_color
                    logger.info(
                        f"Adding required Epic Color ({epic_color_field}: {epic_color}) to creation fields"
                    )
                else:
                    # Store for post-creation update
                    kwargs["__epic_color_field"] = epic_color_field
                    kwargs["__epic_color_value"] = epic_color
                    logger.info(
                        f"Storing optional Epic Color ({epic_color_field}: {epic_color}) for post-creation update"
                    )

            # Handle any other epic-related fields
            for key, value in list(kwargs.items()):
                if key.startswith("epic_") and key in field_ids:
                    field_key = key.replace("epic_", "")
                    field_id = field_ids[key]

                    # Check if this field is required
                    if field_id in required_fields:
                        # Add to fields for initial creation
                        fields[field_id] = value
                        logger.info(
                            f"Adding required Epic field ({field_id} from {key}: {value}) to creation fields"
                        )
                    else:
                        # Store for post-creation update
                        kwargs[f"__epic_{field_key}_field"] = field_id
                        kwargs[f"__epic_{field_key}_value"] = value
                        logger.info(
                            f"Storing optional Epic field ({field_id} from {key}: {value}) for post-creation update"
                        )
                    kwargs.pop(key)  # Remove from kwargs to avoid duplicate processing

            # Warn if epic_name field is required but wasn't discovered
            if not epic_name_field:
                logger.warning(
                    "Could not find Epic Name field ID. Epic creation may fail. "
                    "Consider setting it explicitly in your kwargs."
                )

        except Exception as e:
            logger.error(f"Error preparing Epic-specific fields: {str(e)}")

    def _get_epic_name_field_id(self, field_ids: dict[str, str]) -> str | None:
        """
        Get the field ID for Epic Name, using multiple strategies.

        Args:
            field_ids: Dictionary of field IDs

        Returns:
            The field ID for Epic Name if found, None otherwise
        """
        # Strategy 1: Check if already discovered by get_field_ids_to_epic
        if "epic_name" in field_ids:
            return field_ids["epic_name"]
        if "Epic Name" in field_ids:
            return field_ids["Epic Name"]

        # Strategy 2: Check common field IDs used across different Jira instances
        common_ids = ["customfield_10011", "customfield_10005", "customfield_10004"]
        for field_id in common_ids:
            if field_id in field_ids.values():
                logger.debug(f"Using common Epic Name field ID: {field_id}")
                return field_id

        # Strategy 3: Try to find by dynamic name pattern in available fields
        for key, value in field_ids.items():
            if (
                "epic" in key.lower() and "name" in key.lower()
            ) or "epicname" in key.lower():
                logger.debug(f"Found potential Epic Name field by pattern: {value}")
                return value

        logger.debug("Could not determine Epic Name field ID")
        return None

    def _get_epic_color_field_id(self, field_ids: dict[str, str]) -> str | None:
        """
        Get the field ID for Epic Color, using multiple strategies.

        Args:
            field_ids: Dictionary of field IDs

        Returns:
            The field ID for Epic Color if found, None otherwise
        """
        # Strategy 1: Check if already discovered by get_field_ids_to_epic
        if "epic_color" in field_ids:
            return field_ids["epic_color"]
        if "epic_colour" in field_ids:
            return field_ids["epic_colour"]

        # Strategy 2: Check common field IDs
        common_ids = ["customfield_10012", "customfield_10013"]
        for field_id in common_ids:
            if field_id in field_ids.values():
                logger.debug(f"Using common Epic Color field ID: {field_id}")
                return field_id

        # Strategy 3: Find by dynamic name pattern
        for key, value in field_ids.items():
            if "epic" in key.lower() and (
                "color" in key.lower() or "colour" in key.lower()
            ):
                logger.debug(f"Found potential Epic Color field by pattern: {value}")
                return value

        logger.debug("Could not determine Epic Color field ID")
        return None

    def _is_epic_issue(self, issue: dict[str, Any]) -> bool:
        """Return whether project metadata identifies an issue as an Epic."""
        fields = issue.get("fields", {})
        if not isinstance(fields, dict):
            return False

        issue_type = fields.get("issuetype", {})
        if not isinstance(issue_type, dict):
            return False

        project = fields.get("project", {})
        project_key = project.get("key") if isinstance(project, dict) else None
        issue_type_id = issue_type.get("id")
        if isinstance(project_key, str) and project_key and issue_type_id is not None:
            epic_type_id = self._find_epic_issue_type_id(project_key)
            if epic_type_id is not None:
                return str(issue_type_id) == epic_type_id

        issue_type_name = issue_type.get("name", "")
        return isinstance(issue_type_name, str) and self._is_epic_issue_type(
            issue_type_name
        )

    @staticmethod
    def _epic_link_value_matches(value: Any, epic_key: str) -> bool:
        """Check whether a stored field value points at the given epic.

        Args:
            value: Raw field value as returned by the Jira API. Epic Link
                custom fields hold the epic key as a plain string; ``parent``
                holds a nested issue object.
            epic_key: The epic key the value is expected to identify.

        Returns:
            True if the value identifies the epic, False otherwise.
        """
        expected = epic_key.strip()
        if isinstance(value, str):
            return value.strip() == expected
        if isinstance(value, dict):
            for nested_key in ("key", "id"):
                nested = value.get(nested_key)
                if isinstance(nested, str) and nested.strip() == expected:
                    return True
        return False

    def _epic_link_written(self, issue_key: str, field_id: str, epic_key: str) -> bool:
        """Read one field back and confirm it now stores the epic key.

        A Jira PUT that changes nothing still answers ``204 No Content``, so an
        accepted request is not evidence of a write. Measured on Server/DC: a
        ``parent`` update was accepted while the field stayed null, ``updated``
        was unchanged and the changelog stayed empty, yet the caller was told
        the link succeeded. Only the stored value settles it.

        The read requests just the one field so the verification stays cheap
        and cannot be confused with the full issue fetch used for the result.

        Args:
            issue_key: The key of the issue that was updated.
            field_id: The ID of the field that was written.
            epic_key: The epic key the field is expected to hold.

        Returns:
            True if the field now holds the epic key. False if it does not, or
            if the field could not be read back at all -- an unreadable field
            is reported as a failure on purpose, because a loud false negative
            is recoverable while a silent false positive is not.
        """
        try:
            stored = self.jira.get_issue(issue_key, fields=field_id)
        except Exception as e:
            # The underlying client raises library-specific and requests
            # exceptions here; any of them means "not verified".
            logger.info(
                f"Could not read field {field_id} back from {issue_key} to "
                f"verify the epic link: {str(e)}"
            )
            return False

        if isinstance(stored, str):
            # atlassian-python-api hands back the raw body as a string on Jira
            # Server/DC when ``response.json()`` fails; ``IssuesMixin`` carries
            # the same ``json.loads`` workaround for its own read-back. Without
            # it that transport quirk turns a write that did land into a raised
            # "could not link" error on the very instance family this
            # verification was written for.
            try:
                stored = json.loads(stored)
            except (ValueError, TypeError):
                logger.warning(
                    f"Verification read of {issue_key} returned an unparseable "
                    f"string payload, so {field_id} could not be confirmed"
                )
                return False

        if not isinstance(stored, dict):
            logger.warning(
                f"Unexpected return value type from `jira.get_issue`: {type(stored)}"
            )
            return False

        stored_fields = stored.get("fields")
        if not isinstance(stored_fields, dict):
            logger.info(
                f"Verification read of {issue_key} returned no fields, so "
                f"{field_id} could not be confirmed"
            )
            return False

        return self._epic_link_value_matches(stored_fields.get(field_id), epic_key)

    @staticmethod
    def _is_epic_link_field_definition(definition: Any) -> bool:
        """Check whether a Jira field definition describes the Epic Link field.

        Mirrors the discovery rule in ``FieldsMixin.get_field_ids_to_epic`` so
        a field is accepted here for exactly the reasons it can be discovered
        there: the Jira Software schema key, an ``Epic Link``-shaped display
        name, or the field ID Jira Cloud ships Epic Link under.

        Args:
            definition: One entry from ``get_fields()``.

        Returns:
            True if the definition describes an Epic Link field.
        """
        if not isinstance(definition, dict):
            return False

        schema = definition.get("schema")
        if isinstance(schema, dict) and schema.get("custom") == EPIC_LINK_FIELD_SCHEMA:
            return True

        if definition.get("id") == CLOUD_EPIC_LINK_FIELD_ID:
            return True

        name = definition.get("name")
        if not isinstance(name, str):
            return False
        normalized = name.strip().lower()
        return normalized in ("epic link", "epic") or "epic link" in normalized

    def _epic_link_candidate_is_credible(self, field_id: str) -> bool:
        """Cross-check a discovered Epic Link field against this instance.

        ``get_field_ids_to_epic`` maps every field definition's name onto its
        result dictionary, so a definition literally named ``epic_link`` lands
        on the same key the genuine Epic Link discovery uses -- and wins if it
        is processed later. That is how the fabricated
        ``{"id": <guess>, "name": "epic_link"}`` entry ``get_epic_issues`` used
        to append to ``_field_ids_cache`` became this function's first write
        candidate. Writing an epic key into whatever such a guess points at is
        the defect the blind custom-field sweep was deleted for: on the
        measured Server/DC instance ``customfield_10008`` is Epic Name, a plain
        string field that accepts the write, so the read-back would confirm it
        and the caller would be told the issue had been linked while in fact
        only the epic name field had been clobbered.

        The already-cached definitions are read directly rather than through
        ``get_fields()``: the check must not itself trigger a field fetch, and
        a fabricated definition can only ever live in that cache.
        ``get_field_ids_to_epic`` has just populated it on any instance whose
        field list is readable, and where it is not readable no ``epic_link``
        is discovered in the first place.

        Args:
            field_id: The field ID discovered under the ``epic_link`` key.

        Returns:
            True if this instance's own field definitions describe ``field_id``
            as an Epic Link field. Also True when no definitions are cached or
            none mentions the field at all: an unverifiable candidate is still
            attempted, because the stored-value read-back then decides it.
            False only when the cached definitions positively describe the
            field as something else.
        """
        definitions = self._field_ids_cache
        if not isinstance(definitions, list) or not definitions:
            # No field definitions to cross-check against; keep the
            # discovered field and let the read-back decide.
            return True

        matching = [
            definition
            for definition in definitions
            if isinstance(definition, dict) and definition.get("id") == field_id
        ]
        if not matching:
            return True

        return any(
            self._is_epic_link_field_definition(definition) for definition in matching
        )

    def link_issue_to_epic(self, issue_key: str, epic_key: str) -> JiraIssue:
        """
        Link an existing issue to an epic.

        Each candidate field is written and then read back, so the issue is
        only reported as linked once the epic key is actually stored on it.

        Args:
            issue_key: The key of the issue to link (e.g. 'PROJ-123')
            epic_key: The key of the epic to link to (e.g. 'PROJ-456')

        Returns:
            JiraIssue: The updated issue

        Raises:
            ValueError: If the epic_key is not an actual epic, or if no
                candidate field could be verified as holding the epic key
                after the update
            TypeError: If the Jira API returns an unexpected payload type
            Exception: If there is an error linking the issue to the epic
        """
        try:
            # Verify that both issue and epic exist
            issue = self.jira.get_issue(issue_key)
            epic = self.jira.get_issue(epic_key)
            if not isinstance(issue, dict):
                msg = (
                    f"Unexpected return value type from `jira.get_issue`: {type(issue)}"
                )
                logger.error(msg)
                raise TypeError(msg)
            if not isinstance(epic, dict):
                msg = (
                    f"Unexpected return value type from `jira.get_issue`: {type(epic)}"
                )
                logger.error(msg)
                raise TypeError(msg)

            # Check if the epic key corresponds to an actual epic
            if not self._is_epic_issue(epic):
                error_msg = f"Error linking issue to epic: {epic_key} is not an Epic"
                raise ValueError(error_msg)

            # Get the dynamic field IDs for this Jira instance
            field_ids = self.get_field_ids_to_epic()

            # Ordered link candidates. The discovered Epic Link custom field is
            # tried first because it is the mechanism that actually writes on
            # Server/DC; ``parent`` is the Cloud team-managed mechanism and the
            # fallback. There is deliberately no ``is_cloud`` gate: every
            # attempt is verified by reading the stored value back, so a wrong
            # first candidate costs one no-op request instead of a wrong answer.
            candidates: list[tuple[str, Any]] = []
            epic_link_field = field_ids.get("epic_link")
            if epic_link_field and not self._epic_link_candidate_is_credible(
                epic_link_field
            ):
                logger.warning(
                    f"Ignoring discovered epic link field {epic_link_field}: this "
                    "Jira instance describes it as a different field, so writing "
                    f"{epic_key} into it would overwrite unrelated data on "
                    f"{issue_key}."
                )
                epic_link_field = None
            if epic_link_field:
                candidates.append((epic_link_field, epic_key))
            candidates.append(("parent", {"key": epic_key}))

            for field_id, field_value in candidates:
                try:
                    self.jira.update_issue(
                        issue_key=issue_key,
                        update={"fields": {field_id: field_value}},
                    )
                except Exception as e:
                    logger.info(
                        f"Couldn't link {issue_key} to {epic_key} using field "
                        f"{field_id}: {str(e)}. Trying the next candidate..."
                    )
                    continue

                if self._epic_link_written(issue_key, field_id, epic_key):
                    logger.info(
                        f"Successfully linked {issue_key} to {epic_key} using "
                        f"field {field_id}"
                    )
                    return self.get_issue(issue_key)

                logger.info(
                    f"Update of field {field_id} on {issue_key} was accepted "
                    f"but did not store {epic_key}. Trying the next candidate..."
                )

            # Every candidate was either rejected or silently discarded, so no
            # epic link exists. Never report success here: an unverified write
            # is what PROPX-398 was opened about.
            raise ValueError(
                f"Could not link issue {issue_key} to epic {epic_key}. "
                "No epic link field on the issue holds the epic key after the "
                "update, so your Jira instance might use a different field "
                "for epic links."
            )

        except ValueError:
            # Re-raise ValueError as is
            raise
        except Exception as e:
            logger.error(f"Error linking {issue_key} to epic {epic_key}: {str(e)}")
            # Ensure exception messages follow the expected format for tests
            if "API error" in str(e):
                raise Exception(f"Error linking issue to epic: {str(e)}")
            raise

    def get_epic_issues(
        self, epic_key: str, start: int = 0, limit: int = 50
    ) -> list[JiraIssue]:
        """
        Get all issues linked to a specific epic.

        Args:
            epic_key: The key of the epic (e.g. 'PROJ-123')
            start: Starting index for pagination
            limit: Maximum number of issues to return

        Returns:
            List of JiraIssue models representing the issues linked to the epic

        Raises:
            ValueError: If the issue is not an Epic
            Exception: If there is an error getting epic issues
        """
        try:
            # First, check if the issue is an Epic
            epic = self.jira.get_issue(epic_key)
            if not isinstance(epic, dict):
                msg = (
                    f"Unexpected return value type from `jira.get_issue`: {type(epic)}"
                )
                logger.error(msg)
                raise TypeError(msg)
            fields_data = epic.get("fields", {})

            # Check if the issue is an Epic
            issuetype_data = fields_data.get("issuetype", {})
            issue_type_name = issuetype_data.get("name", "")

            if not self._is_epic_issue(epic):
                # Try to verify via JQL as a fallback
                is_epic = False
                try:
                    verify_jql = f'key = "{epic_key}" AND issuetype = Epic'
                    verify_result = self.search_issues(verify_jql, limit=1)
                    if verify_result and len(verify_result.issues) > 0:
                        is_epic = True
                except Exception as e:
                    # If JQL fails, stick with our previous determination
                    logger.debug(f"JQL verification failed: {e}")

                if not is_epic:
                    error_msg = (
                        f"Issue {epic_key} is not an Epic, it is a "
                        f"{issue_type_name or 'unknown type'}"
                    )
                    raise ValueError(error_msg)

            # Track which methods we've tried
            tried_methods = set()
            issues = []

            # Find the Epic Link field
            field_ids = self.get_field_ids_to_epic()
            epic_link_field = self._find_epic_link_field(field_ids)

            # METHOD 1: Try with 'issueFunction in issuesScopedToEpic' - this works in many Jira instances
            if "issueFunction" not in tried_methods:
                tried_methods.add("issueFunction")
                try:
                    jql = f'issueFunction in issuesScopedToEpic("{epic_key}")'
                    logger.info(f"Trying to get epic issues with issueFunction: {jql}")

                    search_result = self.search_issues(jql, start=start, limit=limit)
                    # A JiraSearchResult is always truthy (a populated model),
                    # so gate on its issues: an empty result must fall through
                    # to the parent / Epic Link strategies below rather than
                    # short-circuiting the whole discovery chain with [].
                    if search_result.issues:
                        logger.info(
                            f"Successfully found {len(search_result.issues)} issues for epic {epic_key} using issueFunction"
                        )
                        return search_result.issues
                except Exception as e:
                    # Log exception but continue with fallback
                    logger.warning(
                        f"Error searching epic issues with issueFunction: {str(e)}"
                    )

            # METHOD 2: Try using parent relationship - common in many Jira setups
            if "parent" not in tried_methods:
                tried_methods.add("parent")
                try:
                    jql = f'parent = "{epic_key}"'
                    logger.info(
                        f"Trying to get epic issues with parent relationship: {jql}"
                    )
                    issues = self._get_epic_issues_by_jql(epic_key, jql, start, limit)
                    if issues:
                        logger.info(
                            f"Successfully found {len(issues)} issues for epic {epic_key} using parent relationship"
                        )
                        return issues
                except Exception as parent_error:
                    logger.warning(
                        f"Error with parent relationship approach: {str(parent_error)}"
                    )

            # METHOD 3: If we found an Epic Link field, try using it
            if epic_link_field and "epicLinkField" not in tried_methods:
                tried_methods.add("epicLinkField")
                try:
                    jql = f'"{epic_link_field}" = "{epic_key}"'
                    logger.info(
                        f"Trying to get epic issues with epic link field: {jql}"
                    )
                    issues = self._get_epic_issues_by_jql(epic_key, jql, start, limit)
                    if issues:
                        logger.info(
                            f"Successfully found {len(issues)} issues for epic {epic_key} using epic link field {epic_link_field}"
                        )
                        return issues
                except Exception as e:
                    logger.warning(
                        f"Error using epic link field {epic_link_field}: {str(e)}"
                    )

            # METHOD 4: Try using 'Epic Link' as a textual field name
            if "epicLinkName" not in tried_methods:
                tried_methods.add("epicLinkName")
                try:
                    jql = f'"Epic Link" = "{epic_key}"'
                    logger.info(
                        f"Trying to get epic issues with 'Epic Link' field name: {jql}"
                    )
                    issues = self._get_epic_issues_by_jql(epic_key, jql, start, limit)
                    if issues:
                        logger.info(
                            f"Successfully found {len(issues)} issues for epic {epic_key} using 'Epic Link' field name"
                        )
                        return issues
                except Exception as e:
                    logger.warning(f"Error using 'Epic Link' field name: {str(e)}")

            # METHOD 5: Try using issue links with a specific link type
            if "issueLinks" not in tried_methods:
                tried_methods.add("issueLinks")
                try:
                    # Try to find issues linked to this epic with standard link types
                    link_types = ["relates to", "blocks", "is blocked by", "is part of"]
                    for link_type in link_types:
                        jql = f'issueLink = "{link_type}" and issueLink = "{epic_key}"'
                        logger.info(
                            f"Trying to get epic issues with issue links: {jql}"
                        )
                        try:
                            issues = self._get_epic_issues_by_jql(
                                epic_key, jql, start, limit
                            )
                            if issues:
                                logger.info(
                                    f"Successfully found {len(issues)} issues for epic {epic_key} using issue links with type '{link_type}'"
                                )
                                return issues
                        except Exception:
                            # Just try the next link type
                            continue
                except Exception as e:
                    logger.warning(f"Error using issue links approach: {str(e)}")

            # METHOD 6: Last resort - try each common Epic Link field ID directly
            if "commonFields" not in tried_methods:
                tried_methods.add("commonFields")
                common_epic_fields = [
                    "customfield_10014",
                    "customfield_10008",
                    "customfield_10100",
                    "customfield_10001",
                    "customfield_10002",
                    "customfield_10003",
                    "customfield_10004",
                    "customfield_10005",
                    "customfield_10006",
                    "customfield_10007",
                    "customfield_11703",
                ]

                for field_id in common_epic_fields:
                    try:
                        jql = f'"{field_id}" = "{epic_key}"'
                        logger.info(
                            f"Trying to get epic issues with common field ID: {jql}"
                        )
                        issues = self._get_epic_issues_by_jql(
                            epic_key, jql, start, limit
                        )
                        if issues:
                            logger.info(
                                f"Successfully found {len(issues)} issues for epic {epic_key} using field ID {field_id}"
                            )
                            # Deliberately NOT cached. Appending
                            # {"id": field_id, "name": "epic_link"} to
                            # _field_ids_cache fabricated a field definition:
                            # get_field_ids_to_epic maps every definition name
                            # onto its result, so that entry overwrote the
                            # genuine Epic Link discovery for the life of the
                            # fetcher and link_issue_to_epic then wrote the
                            # epic key into the guessed field. A JQL match here
                            # only proves the field is queryable, not that it
                            # is the Epic Link field.
                            return issues
                    except Exception:
                        # Just try the next field ID
                        continue

            # If we've tried everything and found no issues, return an empty list
            logger.warning(
                f"No issues found for epic {epic_key} after trying multiple approaches"
            )
            return []

        except ValueError:
            # Re-raise ValueError (like "not an Epic") as is
            raise
        except Exception as e:
            # Wrap other exceptions
            logger.error(f"Error getting issues for epic {epic_key}: {str(e)}")
            raise Exception(f"Error getting epic issues: {str(e)}") from e

    def _find_epic_link_field(self, field_ids: dict[str, str]) -> str | None:
        """
        Find the Epic Link field with fallback mechanisms.

        Args:
            field_ids: Dictionary of field IDs

        Returns:
            The field ID for Epic Link if found, None otherwise
        """
        # First try the standard field name with case-insensitive matching
        for name in ["epic_link", "epiclink", "Epic Link", "epic link", "EPIC LINK"]:
            if name in field_ids:
                logger.info(
                    f"Found Epic Link field by name: {name} -> {field_ids[name]}"
                )
                return field_ids[name]

        # Next, look for any field ID that contains "epic" and "link"
        # (case-insensitive) in the name
        for field_name, field_id in field_ids.items():
            if (
                isinstance(field_name, str)
                and "epic" in field_name.lower()
                and "link" in field_name.lower()
            ):
                logger.info(
                    f"Found potential Epic Link field: {field_name} -> {field_id}"
                )
                return field_id

        # Look for any customfield that might be an epic link
        # Common epic link field IDs across different Jira instances
        known_epic_fields = [
            "customfield_10014",  # Common in Jira Cloud
            "customfield_10008",  # Common in Jira Server
            "customfield_10100",
            "customfield_10001",
            "customfield_10002",
            "customfield_10003",
            "customfield_10004",
            "customfield_10005",
            "customfield_10006",
            "customfield_10007",
            "customfield_11703",  # Added based on error message
        ]

        # Check if any of these known fields exist in our field IDs values
        for field_id in known_epic_fields:
            if field_id in field_ids.values():
                logger.info(f"Using known epic link field ID: {field_id}")
                return field_id

        # Try with common system names for epic link field
        system_names = ["system.epic-link", "com.pyxis.greenhopper.jira:gh-epic-link"]
        for name in system_names:
            if name in field_ids:
                logger.info(
                    f"Found Epic Link field by system name: {name} -> {field_ids[name]}"
                )
                return field_ids[name]

        # If we still can't find it, try to detect it from issue links
        try:
            # Try to find an existing epic
            epics = self._find_sample_epic()
            if epics:
                epic_key = epics[0].get("key")
                if not isinstance(epic_key, str):
                    msg = f"Unexpected return value type from `_find_sample_epic`: {type(epic_key)}"
                    logger.error(msg)
                    raise TypeError(msg)

                # Try to find issues linked to this epic
                issues = self._find_issues_linked_to_epic(epic_key)
                for issue in issues:
                    # Check fields for any that contain the epic key
                    fields = issue.get("fields", {})
                    for field_id, value in fields.items():
                        if (
                            field_id.startswith("customfield_")
                            and isinstance(value, str)
                            and value == epic_key
                        ):
                            logger.info(
                                f"Detected epic link field {field_id} from linked issue"
                            )
                            return field_id
        except Exception as e:
            logger.warning(f"Error detecting epic link field from issues: {str(e)}")

        # As a last resort, look for any customfield that starts with customfield_
        # and has "epic" in its schema name or description
        try:
            all_fields = self.jira.get_all_fields()
            if not isinstance(all_fields, list):
                msg = f"Unexpected return value type from `jira.get_all_fields`: {type(all_fields)}"
                logger.error(msg)
                raise TypeError(msg)

            for field in all_fields:
                field_id = field.get("id", "")
                schema = field.get("schema", {})
                custom_type = schema.get("custom", "")
                if field_id.startswith("customfield_") and (
                    "epic" in custom_type.lower()
                    or "epic" in field.get("name", "").lower()
                    or "epic" in field.get("description", "").lower()
                ):
                    logger.info(
                        f"Found potential Epic Link field by schema inspection: {field_id}"
                    )
                    return field_id
        except Exception as e:
            logger.warning(
                f"Error during schema inspection for Epic Link field: {str(e)}"
            )

        # No Epic Link field found
        logger.warning("Could not determine Epic Link field with any method")
        return None

    def _find_sample_epic(self) -> list[dict]:
        """
        Find a sample epic to use for detecting the epic link field.

        Returns:
            List of epics found
        """
        try:
            # Search for issues with type=Epic
            jql = "issuetype = Epic ORDER BY updated DESC"
            response = self.jira.jql(jql, limit=1)
            if not isinstance(response, dict):
                msg = f"Unexpected return value type from `jira.jql`: {type(response)}"
                logger.error(msg)
                raise TypeError(msg)

            if response and "issues" in response and response["issues"]:
                return response["issues"]
        except Exception as e:
            logger.warning(f"Error finding sample epic: {str(e)}")
        return []

    def _find_issues_linked_to_epic(self, epic_key: str) -> list[dict]:
        """
        Find issues linked to a specific epic.

        Args:
            epic_key: The key of the epic

        Returns:
            List of issues found
        """
        try:
            # Try several JQL formats to find linked issues
            for query in [
                f"'Epic Link' = {epic_key}",
                f"'Epic' = {epic_key}",
                f"parent = {epic_key}",
                f"issueFunction in issuesScopedToEpic('{epic_key}')",
            ]:
                try:
                    response = self.jira.jql(query, limit=5)
                    if not isinstance(response, dict):
                        msg = f"Unexpected return value type from `jira.jql`: {type(response)}"
                        logger.error(msg)
                        raise TypeError(msg)
                    if response.get("issues"):
                        return response["issues"]
                except Exception:
                    # Try next query format
                    continue
        except Exception as e:
            logger.warning(f"Error finding issues linked to epic {epic_key}: {str(e)}")
        return []

    def _get_epic_issues_by_jql(
        self, epic_key: str, jql: str, start: int, limit: int
    ) -> list[JiraIssue]:
        """
        Helper method to get issues using a JQL query.

        Args:
            epic_key: The key of the epic
            jql: JQL query to execute
            start: Starting index for pagination
            limit: Maximum number of issues to return

        Returns:
            List of JiraIssue models
        """

        search_result = self.search_issues(jql, start=start, limit=limit)
        if not search_result:
            logger.warning(f"No issues found for epic {epic_key} with query: {jql}")
        return search_result.issues

    def update_epic_fields(self, issue_key: str, kwargs: dict[str, Any]) -> JiraIssue:
        """
        Update Epic-specific fields after Epic creation.

        This method implements the second step of the two-step Epic creation process,
        applying Epic-specific fields that may be rejected during initial creation
        due to screen configuration restrictions.

        Args:
            issue_key: The key of the created Epic
            kwargs: Dictionary containing special keys with Epic field information

        Returns:
            JiraIssue: The updated Epic

        Raises:
            Exception: If there is an error updating the Epic fields
        """
        try:
            # Extract Epic fields from kwargs
            update_fields = {}

            # Process Epic Name field
            if "__epic_name_field" in kwargs and "__epic_name_value" in kwargs:
                epic_name_field = kwargs.pop("__epic_name_field")
                epic_name_value = kwargs.pop("__epic_name_value")
                update_fields[epic_name_field] = epic_name_value
                logger.info(
                    f"Updating Epic Name field ({epic_name_field}): {epic_name_value}"
                )

            # Process Epic Color field
            if "__epic_color_field" in kwargs and "__epic_color_value" in kwargs:
                epic_color_field = kwargs.pop("__epic_color_field")
                epic_color_value = kwargs.pop("__epic_color_value")
                update_fields[epic_color_field] = epic_color_value
                logger.info(
                    f"Updating Epic Color field ({epic_color_field}): {epic_color_value}"
                )

            # Process any other stored Epic fields
            epic_field_keys = [
                k for k in kwargs if k.startswith("__epic_") and k.endswith("_field")
            ]
            for field_key in epic_field_keys:
                # Get corresponding value key
                value_key = field_key.replace("_field", "_value")
                if value_key in kwargs:
                    field_id = kwargs.pop(field_key)
                    field_value = kwargs.pop(value_key)
                    update_fields[field_id] = field_value
                    logger.info(f"Updating Epic field ({field_id}): {field_value}")

            # If we have fields to update, make the API call
            if update_fields:
                logger.info(f"Updating Epic {issue_key} with fields: {update_fields}")
                try:
                    # First try using the generic update_issue method
                    self.jira.update_issue(issue_key, update={"fields": update_fields})
                    logger.info(
                        f"Successfully updated Epic {issue_key} with Epic-specific fields"
                    )
                except Exception as update_error:
                    # Log the error but don't raise yet - try alternative approaches
                    logger.warning(
                        f"Error updating Epic with primary method: {str(update_error)}"
                    )

                    # Try updating fields one by one as fallback
                    success = False
                    for field_id, field_value in update_fields.items():
                        try:
                            self.jira.update_issue(
                                issue_key, update={"fields": {field_id: field_value}}
                            )
                            logger.info(
                                f"Successfully updated Epic field {field_id} with value {field_value}"
                            )
                            success = True
                        except Exception as field_error:
                            logger.warning(
                                f"Failed to update Epic field {field_id}: {str(field_error)}"
                            )

                    # If even individual updates failed, log but continue
                    if not success:
                        logger.error(
                            f"Failed to update Epic {issue_key} with Epic-specific fields. "
                            f"The Epic was created but may be missing required attributes."
                        )

            # Return the updated Epic
            return self.get_issue(issue_key)

        except Exception as e:
            logger.error(f"Error in update_epic_fields: {str(e)}")
            # Return the Epic even if the update failed
            return self.get_issue(issue_key)
