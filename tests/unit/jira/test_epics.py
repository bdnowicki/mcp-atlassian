"""Tests for the Jira Epics mixin."""

import json
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from mcp_atlassian.jira import JiraFetcher
from mcp_atlassian.jira.config import JiraConfig
from mcp_atlassian.jira.epics import EpicsMixin
from mcp_atlassian.models.jira import JiraIssue


class TestEpicsMixin:
    """Tests for the EpicsMixin class."""

    @pytest.fixture
    def epics_mixin(self, jira_fetcher: JiraFetcher) -> EpicsMixin:
        """Create an EpicsMixin instance with mocked dependencies."""
        mixin = jira_fetcher

        # Add a mock for get_issue to use when returning models
        mixin.get_issue = MagicMock(
            return_value=JiraIssue(
                id="12345",
                key="TEST-123",
                summary="Test Issue",
                description="Issue content",
            )
        )

        # Add a mock for search_issues to use for get_epic_issues
        mixin.search_issues = MagicMock(
            return_value=[
                JiraIssue(key="TEST-456", summary="Issue 1"),
                JiraIssue(key="TEST-789", summary="Issue 2"),
            ]
        )

        return mixin

    def test_try_discover_fields_from_existing_epic(self, epics_mixin: EpicsMixin):
        """Test _try_discover_fields_from_existing_epic with a successful discovery."""
        # Skip if we already have both required fields
        field_ids = {"epic_link": "customfield_10014"}  # Missing epic_name

        # Mock Epic search response
        mock_epic = {
            "key": "EPIC-123",
            "fields": {
                "issuetype": {"name": "Epic"},
                "summary": "Test Epic",
                "customfield_10011": "Epic Name Value",  # This should be discovered as epic_name
            },
        }

        mock_results = {"issues": [mock_epic]}
        epics_mixin.jira.jql.return_value = mock_results

        # Call the method
        epics_mixin._try_discover_fields_from_existing_epic(field_ids)

        # Verify the epic_name field was discovered
        assert "epic_name" in field_ids
        assert field_ids["epic_name"] == "customfield_10011"

    def test_try_discover_fields_from_existing_epic_no_epics(
        self, epics_mixin: EpicsMixin
    ):
        """Test _try_discover_fields_from_existing_epic when no epics exist."""
        field_ids = {}

        # Mock empty search response
        mock_results = {"issues": []}
        epics_mixin.jira.jql.return_value = mock_results

        # Call the method
        epics_mixin._try_discover_fields_from_existing_epic(field_ids)

        # Verify no fields were discovered
        assert not field_ids

    def test_try_discover_fields_from_existing_epic_with_both_fields(
        self, epics_mixin: EpicsMixin
    ):
        """Test _try_discover_fields_from_existing_epic when both fields already exist."""
        field_ids = {"epic_link": "customfield_10014", "epic_name": "customfield_10011"}

        # Call the method - no JQL should be executed
        epics_mixin._try_discover_fields_from_existing_epic(field_ids)

        # Verify jql was not called
        epics_mixin.jira.jql.assert_not_called()

    def test_prepare_epic_fields_basic(self, epics_mixin: EpicsMixin):
        """Test prepare_epic_fields with basic epic name and color."""
        # Mock get_field_ids_to_epic
        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={
                "epic_name": "customfield_10011",
                "epic_color": "customfield_10010",
            }
        )

        # Prepare test data
        fields = {}
        summary = "Test Epic"
        kwargs = {}

        # Call the method
        epics_mixin.prepare_epic_fields(fields, summary, kwargs)

        # Verify the epic fields are stored in kwargs with __epic_ prefix
        # instead of directly in fields (for two-step creation)
        assert kwargs["__epic_name_value"] == "Test Epic"
        assert kwargs["__epic_name_field"] == "customfield_10011"
        assert kwargs["__epic_color_value"] == "green"
        assert kwargs["__epic_color_field"] == "customfield_10010"
        # Verify fields dict remains empty
        assert fields == {}

    def test_prepare_epic_fields_with_user_values(self, epics_mixin: EpicsMixin):
        """Test prepare_epic_fields with user-provided values."""
        # Mock get_field_ids_to_epic
        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={
                "epic_name": "customfield_10011",
                "epic_color": "customfield_10010",
            }
        )

        # Prepare test data
        fields = {}
        summary = "Test Epic"
        kwargs = {"epic_name": "Custom Epic Name", "epic_color": "blue"}

        # Call the method
        epics_mixin.prepare_epic_fields(fields, summary, kwargs)

        # Verify the epic fields are stored in kwargs with __epic_ prefix
        assert kwargs["__epic_name_value"] == "Custom Epic Name"
        assert kwargs["__epic_name_field"] == "customfield_10011"
        assert kwargs["__epic_color_value"] == "blue"
        assert kwargs["__epic_color_field"] == "customfield_10010"

        # Original values should be removed from kwargs
        assert "epic_name" not in kwargs
        assert "epic_color" not in kwargs

        # Verify fields dict remains empty
        assert fields == {}

    def test_prepare_epic_fields_missing_epic_name(self, epics_mixin: EpicsMixin):
        """Test prepare_epic_fields with missing epic_name field."""
        # Mock get_field_ids_to_epic
        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_color": "customfield_10010"}
        )

        # Prepare test data
        fields = {}
        summary = "Test Epic"
        kwargs = {}

        # Call the method
        epics_mixin.prepare_epic_fields(fields, summary, kwargs)

        # Verify only the color was stored in kwargs
        assert "__epic_name_value" not in kwargs
        assert "__epic_name_field" not in kwargs
        assert kwargs["__epic_color_value"] == "green"
        assert kwargs["__epic_color_field"] == "customfield_10010"

        # Verify fields dict remains empty
        assert fields == {}

    def test_prepare_epic_fields_with_error(self, epics_mixin: EpicsMixin):
        """Test prepare_epic_fields catches and logs errors."""
        # Mock get_field_ids_to_epic to raise an exception
        epics_mixin.get_field_ids_to_epic = MagicMock(
            side_effect=Exception("Field error")
        )

        # Create the fields dict and call the method
        fields = {}
        epics_mixin.prepare_epic_fields(fields, "Test Epic", {})

        # Verify that fields didn't get updated
        assert fields == {}
        # Verify the error was logged
        epics_mixin.get_field_ids_to_epic.assert_called_once()

    def test_prepare_epic_fields_with_non_standard_ids(self, epics_mixin: EpicsMixin):
        """Test that prepare_epic_fields correctly handles non-standard field IDs."""
        # Mock field IDs with non-standard custom field IDs
        mock_field_ids = {
            "epic_name": "customfield_54321",
            "epic_color": "customfield_98765",
        }

        # Mock the get_field_ids_to_epic method to return our custom field IDs
        epics_mixin.get_field_ids_to_epic = MagicMock(return_value=mock_field_ids)

        # Create the fields dict and call the method with basic values
        fields = {}
        kwargs = {}
        epics_mixin.prepare_epic_fields(fields, "Test Epic", kwargs)

        # Verify fields were stored in kwargs with the non-standard IDs
        assert kwargs["__epic_name_value"] == "Test Epic"
        assert kwargs["__epic_name_field"] == "customfield_54321"
        assert kwargs["__epic_color_value"] == "green"
        assert kwargs["__epic_color_field"] == "customfield_98765"

        # Verify fields dict remains empty
        assert fields == {}

        # Test with custom values
        fields = {}
        kwargs = {"epic_name": "Custom Name", "epic_color": "blue"}
        epics_mixin.prepare_epic_fields(fields, "Test Epic", kwargs)

        # Verify custom values were stored in kwargs
        assert kwargs["__epic_name_value"] == "Custom Name"
        assert kwargs["__epic_name_field"] == "customfield_54321"
        assert kwargs["__epic_color_value"] == "blue"
        assert kwargs["__epic_color_field"] == "customfield_98765"

        # Original values should be removed from kwargs
        assert "epic_name" not in kwargs
        assert "epic_color" not in kwargs

        # Verify fields dict remains empty
        assert fields == {}

    def test_prepare_epic_fields_with_required_epic_name(self, epics_mixin: EpicsMixin):
        """Test Epic field preparation when Epic Name is a required field."""
        epics_mixin._find_epic_issue_type_id = MagicMock(return_value="10001")
        # Mock get_field_ids_to_epic to return field IDs
        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={
                "epic_name": "customfield_10011",
                "epic_color": "customfield_10012",
            }
        )

        # Mock get_required_fields to return Epic Name as required
        epics_mixin.get_required_fields = MagicMock(
            return_value={
                "customfield_10011": {
                    "fieldId": "customfield_10011",
                    "required": True,
                    "name": "Epic Name",
                }
            }
        )

        fields = {}
        kwargs = {"epic_name": "My Epic Name", "epic_color": "blue"}

        # Call prepare_epic_fields with project_key
        epics_mixin.prepare_epic_fields(fields, "Test Epic", kwargs, "TEST")

        # Assert Epic Name was added to fields for initial creation
        assert fields["customfield_10011"] == "My Epic Name"

        # Assert Epic Color was stored for post-creation update
        assert kwargs["__epic_color_field"] == "customfield_10012"
        assert kwargs["__epic_color_value"] == "blue"

        # Verify get_required_fields was called with correct parameters
        epics_mixin.get_required_fields.assert_called_once_with("10001", "TEST")

    def test_prepare_epic_fields_with_optional_epic_name(self, epics_mixin: EpicsMixin):
        """Test Epic field preparation when Epic Name is not a required field."""
        # Mock get_field_ids_to_epic to return field IDs
        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={
                "epic_name": "customfield_10011",
                "epic_color": "customfield_10012",
            }
        )

        # Mock get_required_fields to return empty dict (no required fields)
        epics_mixin.get_required_fields = MagicMock(return_value={})

        fields = {}
        kwargs = {"epic_name": "My Epic Name", "epic_color": "green"}

        # Call prepare_epic_fields with project_key
        epics_mixin.prepare_epic_fields(fields, "Test Epic", kwargs, "TEST")

        # Assert Epic Name was stored for post-creation update (not in fields)
        assert "customfield_10011" not in fields
        assert kwargs["__epic_name_field"] == "customfield_10011"
        assert kwargs["__epic_name_value"] == "My Epic Name"

        # Assert Epic Color was also stored for post-creation update
        assert kwargs["__epic_color_field"] == "customfield_10012"
        assert kwargs["__epic_color_value"] == "green"

    def test_prepare_epic_fields_mixed_required_optional(self, epics_mixin: EpicsMixin):
        """Test Epic field preparation with mixed required and optional fields."""
        # Mock get_field_ids_to_epic to return field IDs
        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={
                "epic_name": "customfield_10011",
                "epic_color": "customfield_10012",
                "epic_start_date": "customfield_10013",
            }
        )

        # Mock get_required_fields to return Epic Name and Start Date as required
        epics_mixin.get_required_fields = MagicMock(
            return_value={
                "customfield_10011": {"fieldId": "customfield_10011", "required": True},
                "customfield_10013": {"fieldId": "customfield_10013", "required": True},
            }
        )

        fields = {}
        kwargs = {
            "epic_name": "My Epic Name",
            "epic_color": "purple",
            "epic_start_date": "2024-01-01",
        }

        # Call prepare_epic_fields with project_key
        epics_mixin.prepare_epic_fields(fields, "Test Epic", kwargs, "TEST")

        # Assert required fields were added to fields
        assert fields["customfield_10011"] == "My Epic Name"
        assert fields["customfield_10013"] == "2024-01-01"

        # Assert optional field was stored for post-creation update
        assert "customfield_10012" not in fields
        assert kwargs["__epic_color_field"] == "customfield_10012"
        assert kwargs["__epic_color_value"] == "purple"

    def test_prepare_epic_fields_no_project_key(self, epics_mixin: EpicsMixin):
        """Test Epic field preparation when no project_key is provided."""
        # Mock get_field_ids_to_epic to return field IDs
        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={
                "epic_name": "customfield_10011",
                "epic_color": "customfield_10012",
            }
        )

        # Mock get_required_fields should not be called
        epics_mixin.get_required_fields = MagicMock()

        fields = {}
        kwargs = {"epic_name": "My Epic Name", "epic_color": "red"}

        # Call prepare_epic_fields without project_key (None)
        epics_mixin.prepare_epic_fields(fields, "Test Epic", kwargs, None)

        # Assert all fields were stored for post-creation update (fallback behavior)
        assert "customfield_10011" not in fields
        assert "customfield_10012" not in fields
        assert kwargs["__epic_name_field"] == "customfield_10011"
        assert kwargs["__epic_name_value"] == "My Epic Name"
        assert kwargs["__epic_color_field"] == "customfield_10012"
        assert kwargs["__epic_color_value"] == "red"

        # Verify get_required_fields was not called
        epics_mixin.get_required_fields.assert_not_called()

    def test_prepare_epic_fields_get_required_fields_error(
        self, epics_mixin: EpicsMixin
    ):
        """Test Epic field preparation when get_required_fields raises an error."""
        # Mock get_field_ids_to_epic to return field IDs
        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={
                "epic_name": "customfield_10011",
                "epic_color": "customfield_10012",
            }
        )

        # Mock get_required_fields to raise an exception
        epics_mixin.get_required_fields = MagicMock(side_effect=Exception("API error"))

        fields = {}
        kwargs = {"epic_name": "My Epic Name", "epic_color": "yellow"}

        # Call prepare_epic_fields with project_key
        epics_mixin.prepare_epic_fields(fields, "Test Epic", kwargs, "TEST")

        # Assert it falls back to storing all fields for post-creation update
        assert "customfield_10011" not in fields
        assert "customfield_10012" not in fields
        assert kwargs["__epic_name_field"] == "customfield_10011"
        assert kwargs["__epic_name_value"] == "My Epic Name"
        assert kwargs["__epic_color_field"] == "customfield_10012"
        assert kwargs["__epic_color_value"] == "yellow"

    def test_prepare_epic_fields_no_get_required_fields_method(
        self, epics_mixin: EpicsMixin
    ):
        """Test Epic field preparation when get_required_fields method doesn't exist."""
        # Mock get_field_ids_to_epic to return field IDs
        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={
                "epic_name": "customfield_10011",
                "epic_color": "customfield_10012",
            }
        )

        # Mock hasattr to return False for get_required_fields
        original_hasattr = hasattr

        def mock_hasattr(obj, attr):
            if attr == "get_required_fields":
                return False
            return original_hasattr(obj, attr)

        import builtins

        builtins.hasattr = mock_hasattr

        fields = {}
        kwargs = {"epic_name": "My Epic Name", "epic_color": "orange"}

        # Call prepare_epic_fields with project_key
        epics_mixin.prepare_epic_fields(fields, "Test Epic", kwargs, "TEST")

        # Restore original hasattr
        builtins.hasattr = original_hasattr

        # Assert it falls back to storing all fields for post-creation update
        assert "customfield_10011" not in fields
        assert "customfield_10012" not in fields
        assert kwargs["__epic_name_field"] == "customfield_10011"
        assert kwargs["__epic_name_value"] == "My Epic Name"
        assert kwargs["__epic_color_field"] == "customfield_10012"
        assert kwargs["__epic_color_value"] == "orange"

    def test_dynamic_epic_field_discovery(self, epics_mixin: EpicsMixin):
        """Test the dynamic discovery of Epic fields with pattern matching."""
        # Mock get_field_ids_to_epic with no epic-related fields
        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={
                "random_field": "customfield_12345",
                "some_other_field": "customfield_67890",
                "Epic-FieldName": "customfield_11111",  # Should be found by pattern matching
                "epic_colour_field": "customfield_22222",  # Should be found by pattern matching
            }
        )

        # Create a fields dict and call prepare_epic_fields
        fields = {}
        kwargs = {}

        # The _get_epic_name_field_id and _get_epic_color_field_id methods should discover
        # the fields by pattern matching, even though they're not in the standard format

        # We need to patch these methods to return the expected values
        original_get_name = epics_mixin._get_epic_name_field_id
        original_get_color = epics_mixin._get_epic_color_field_id

        epics_mixin._get_epic_name_field_id = MagicMock(
            return_value="customfield_11111"
        )
        epics_mixin._get_epic_color_field_id = MagicMock(
            return_value="customfield_22222"
        )

        # Now call prepare_epic_fields
        epics_mixin.prepare_epic_fields(fields, "Test Epic Name", kwargs)

        # Verify the fields were stored in kwargs
        assert kwargs["__epic_name_value"] == "Test Epic Name"
        assert kwargs["__epic_name_field"] == "customfield_11111"
        assert kwargs["__epic_color_value"] == "green"
        assert kwargs["__epic_color_field"] == "customfield_22222"

        # Verify fields dict remains empty
        assert fields == {}

        # Restore the original methods
        epics_mixin._get_epic_name_field_id = original_get_name
        epics_mixin._get_epic_color_field_id = original_get_color

    def test_link_issue_to_epic_success(self, epics_mixin: EpicsMixin):
        """Test link_issue_to_epic falling through a rejected Epic Link write.

        The discovered Epic Link field is attempted first; when the instance
        rejects it outright the ``parent`` candidate is tried next, and only
        the verified write is reported.
        """
        # Setup mocks
        # - issue exists, epic exists, then the parent verification read-back
        epics_mixin.jira.get_issue.side_effect = [
            {"key": "TEST-123"},  # issue
            {  # epic
                "key": "EPIC-456",
                "fields": {"issuetype": {"name": "Epic"}},
            },
            {  # verification read-back for parent
                "key": "TEST-123",
                "fields": {"parent": {"key": "EPIC-456"}},
            },
        ]

        # Mock get_issue to return a valid JiraIssue
        epics_mixin.get_issue = MagicMock(
            return_value=JiraIssue(key="TEST-123", id="123456")
        )

        # - epic link field discovered
        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10014"}
        )

        # - epic_link is rejected, then parent succeeds
        epics_mixin.jira.update_issue.side_effect = [
            Exception("Field 'customfield_10014' cannot be set"),
            None,
        ]

        # Call the method
        result = epics_mixin.link_issue_to_epic("TEST-123", "EPIC-456")

        # Verify API calls - the discovered field is tried before parent
        assert epics_mixin.jira.update_issue.call_count == 2
        assert epics_mixin.jira.update_issue.call_args_list[0] == call(
            issue_key="TEST-123", update={"fields": {"customfield_10014": "EPIC-456"}}
        )
        assert epics_mixin.jira.update_issue.call_args_list[1] == call(
            issue_key="TEST-123", update={"fields": {"parent": {"key": "EPIC-456"}}}
        )

        # Verify get_issue was called to return the result
        epics_mixin.get_issue.assert_called_once_with("TEST-123")

        # Verify result
        assert isinstance(result, JiraIssue)
        assert result.key == "TEST-123"

    def test_link_issue_to_epic_parent_field_success(self, epics_mixin: EpicsMixin):
        """Test link_issue_to_epic succeeding with a verified parent field."""
        # Setup mocks
        epics_mixin.jira.get_issue.side_effect = [
            {"key": "TEST-123"},  # issue
            {  # epic
                "key": "EPIC-456",
                "fields": {"issuetype": {"name": "Epic"}},
            },
            {  # verification read-back for parent
                "key": "TEST-123",
                "fields": {"parent": {"key": "EPIC-456"}},
            },
        ]

        # Mock get_issue to return a valid JiraIssue
        epics_mixin.get_issue = MagicMock(
            return_value=JiraIssue(key="TEST-123", id="123456")
        )

        # - No epic_link field (forces parent usage)
        epics_mixin.get_field_ids_to_epic = MagicMock(return_value={})

        # Parent field update succeeds
        epics_mixin.jira.update_issue.return_value = None

        # Call the method
        result = epics_mixin.link_issue_to_epic("TEST-123", "EPIC-456")

        # Verify only one API call with parent field
        epics_mixin.jira.update_issue.assert_called_once_with(
            issue_key="TEST-123", update={"fields": {"parent": {"key": "EPIC-456"}}}
        )

        # Verify result
        assert isinstance(result, JiraIssue)
        assert result.key == "TEST-123"

    def test_link_issue_to_epic_not_epic(self, epics_mixin: EpicsMixin):
        """Test link_issue_to_epic when the target is not an epic."""
        # Setup mocks
        epics_mixin.jira.get_issue.side_effect = [
            {"key": "TEST-123"},  # issue
            {  # not an epic
                "key": "TEST-456",
                "fields": {"issuetype": {"name": "Task"}},
            },
        ]

        # Call the method and expect an error
        with pytest.raises(
            ValueError, match="Error linking issue to epic: TEST-456 is not an Epic"
        ):
            epics_mixin.link_issue_to_epic("TEST-123", "TEST-456")

    def test_link_issue_to_localized_epic_by_issue_type_id(
        self, epics_mixin: EpicsMixin
    ):
        """Accept a localized Epic when its stable issue type ID matches."""
        epics_mixin.jira.get_issue.side_effect = [
            {"key": "TEST-123"},
            {
                "key": "EPIC-456",
                "fields": {
                    "project": {"key": "TEST"},
                    "issuetype": {"id": "10001", "name": "Эпик"},
                },
            },
            {  # verification read-back for parent
                "key": "TEST-123",
                "fields": {"parent": {"key": "EPIC-456"}},
            },
        ]
        epics_mixin._find_epic_issue_type_id = MagicMock(return_value="10001")
        epics_mixin.get_field_ids_to_epic = MagicMock(return_value={})

        result = epics_mixin.link_issue_to_epic("TEST-123", "EPIC-456")

        assert result == epics_mixin.get_issue.return_value
        epics_mixin._find_epic_issue_type_id.assert_called_once_with("TEST")

    def test_link_issue_to_epic_all_methods_fail(self, epics_mixin: EpicsMixin):
        """Test link_issue_to_epic when all linking methods fail."""
        # Setup mocks
        epics_mixin.jira.get_issue.side_effect = [
            {"key": "TEST-123"},  # issue
            {  # epic
                "key": "EPIC-456",
                "fields": {"issuetype": {"name": "Epic"}},
            },
        ]

        # No epic link fields found
        epics_mixin.get_field_ids_to_epic = MagicMock(return_value={})

        # All update attempts fail
        epics_mixin.jira.update_issue.side_effect = Exception("Update failed")
        epics_mixin.jira.create_issue_link.side_effect = Exception("Link failed")

        # Call the method and expect a ValueError
        with pytest.raises(
            ValueError,
            match="Could not link issue TEST-123 to epic EPIC-456.",
        ):
            epics_mixin.link_issue_to_epic("TEST-123", "EPIC-456")

    def test_link_issue_to_epic_api_error(self, epics_mixin: EpicsMixin):
        """Test link_issue_to_epic with API error in the epic retrieval."""
        # Setup mocks to fail at epic retrieval
        epics_mixin.jira.get_issue.side_effect = [
            {"key": "TEST-123"},  # issue
            Exception("API error"),  # epic retrieval fails
        ]

        # Call the method and expect the API error to be propagated
        with pytest.raises(Exception, match="Error linking issue to epic: API error"):
            epics_mixin.link_issue_to_epic("TEST-123", "EPIC-456")

    def test_get_epic_issues_success(self, epics_mixin):
        """Test get_epic_issues with successful retrieval."""
        # Setup mocks
        epics_mixin.jira.get_issue.return_value = {
            "key": "EPIC-123",
            "fields": {"issuetype": {"name": "Epic"}},
        }

        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10014"}
        )

        # Create a mock search result object with issues attribute
        mock_issues = [
            JiraIssue(key="TEST-456", summary="Issue 1"),
            JiraIssue(key="TEST-789", summary="Issue 2"),
        ]

        # Create a mock object with an issues attribute
        class MockSearchResult:
            def __init__(self, issues):
                self.issues = issues

        mock_search_result = MockSearchResult(mock_issues)

        # Mock search_issues to return our mock search result
        epics_mixin.search_issues = MagicMock(return_value=mock_search_result)

        # Call the method with start parameter
        result = epics_mixin.get_epic_issues("EPIC-123", start=5, limit=10)

        # Verify search_issues was called with the right JQL
        epics_mixin.search_issues.assert_called_once()
        call_args = epics_mixin.search_issues.call_args[0]
        assert 'issueFunction in issuesScopedToEpic("EPIC-123")' in call_args[0]

        # Verify keyword arguments for start and limit
        call_kwargs = epics_mixin.search_issues.call_args[1]
        assert call_kwargs.get("start") == 5
        assert call_kwargs.get("limit") == 10

        # Verify result
        assert len(result) == 2
        assert result[0].key == "TEST-456"
        assert result[1].key == "TEST-789"

    def test_get_epic_issues_not_epic(self, epics_mixin):
        """Test get_epic_issues when the issue is not an epic."""
        # Setup mocks - issue is not an epic
        epics_mixin.jira.get_issue.return_value = {
            "key": "TEST-123",
            "fields": {"issuetype": {"name": "Task"}},
        }

        # Call the method and expect an error
        with pytest.raises(
            ValueError, match="Issue TEST-123 is not an Epic, it is a Task"
        ):
            epics_mixin.get_epic_issues("TEST-123")

    def test_get_epic_issues_accepts_localized_epic_by_issue_type_id(
        self, epics_mixin: EpicsMixin
    ):
        """Accept a localized Epic when project metadata identifies its type ID."""
        from mcp_atlassian.models.jira import JiraSearchResult

        epics_mixin.jira.get_issue.return_value = {
            "key": "EPIC-123",
            "fields": {
                "project": {"key": "TEST"},
                "issuetype": {"id": "10001", "name": "Эпик"},
            },
        }
        epics_mixin._find_epic_issue_type_id = MagicMock(return_value="10001")
        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10014"}
        )
        epics_mixin.search_issues = MagicMock(
            return_value=JiraSearchResult(
                issues=[JiraIssue(key="TEST-456", summary="Issue 1")],
                total=1,
                start_at=0,
            )
        )

        result = epics_mixin.get_epic_issues("EPIC-123")

        assert [issue.key for issue in result] == ["TEST-456"]
        epics_mixin._find_epic_issue_type_id.assert_called_once_with("TEST")

    def test_get_epic_issues_no_results(self, epics_mixin):
        """Test get_epic_issues when no results are found."""
        # Setup mocks
        epics_mixin.jira.get_issue.return_value = {
            "key": "EPIC-123",
            "fields": {"issuetype": {"name": "Epic"}},
        }

        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10014"}
        )

        # Make search_issues return empty results
        epics_mixin.search_issues = MagicMock(return_value=[])

        # Call the method
        result = epics_mixin.get_epic_issues("EPIC-123")

        # Verify the result is an empty list
        assert isinstance(result, list)
        assert not result

    def test_get_epic_issues_empty_issuefunction_falls_through_real_model(
        self, epics_mixin
    ):
        """An empty issueFunction result must not short-circuit the fallback.

        Regression: ``JiraSearchResult`` is a populated Pydantic model and is
        therefore always truthy, so a bare ``if search_result:`` accepted an
        empty METHOD 1 result and returned ``[]`` without ever trying the
        parent / Epic Link strategies. This uses the REAL model (not a mock
        with ``__bool__``) to prove the empty result falls through.
        """
        from mcp_atlassian.models.jira import JiraSearchResult

        epics_mixin.jira.get_issue.return_value = {
            "key": "EPIC-123",
            "fields": {"issuetype": {"name": "Epic"}},
        }
        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10014", "parent": "parent"}
        )

        def search_side_effect(jql, **kwargs):
            if "issueFunction" in jql:
                # Real model, empty issues -> truthy but must be treated as
                # "no results" and fall through.
                return JiraSearchResult(issues=[], total=0, start_at=0)
            if "parent" in jql:
                return JiraSearchResult(
                    issues=[
                        JiraIssue(key="CHILD-1", summary="Child 1"),
                        JiraIssue(key="CHILD-2", summary="Child 2"),
                    ],
                    total=2,
                    start_at=0,
                )
            return JiraSearchResult(issues=[], total=0, start_at=0)

        epics_mixin.search_issues = MagicMock(side_effect=search_side_effect)

        result = epics_mixin.get_epic_issues("EPIC-123")

        # Fell through to the parent strategy instead of returning [].
        assert [i.key for i in result] == ["CHILD-1", "CHILD-2"]
        assert epics_mixin.search_issues.call_count >= 2

    def test_get_epic_issues_fallback_jql(self, epics_mixin):
        """Test get_epic_issues with fallback JQL queries."""
        # Setup mocks
        epics_mixin.jira.get_issue.return_value = {
            "key": "EPIC-123",
            "fields": {"issuetype": {"name": "Epic"}},
        }

        epics_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10014", "parent": "parent"}
        )

        # Create a mock class for SearchResult
        class MockSearchResult:
            def __init__(self, issues: list[JiraIssue]):
                self.issues = issues

            def __bool__(self):
                return bool(self.issues)

            def __len__(self):
                return len(self.issues)

        # Mock search_issues to return empty results for issueFunction but results for epic_link
        def search_side_effect(jql, **kwargs):
            if "issueFunction" in jql:
                return MockSearchResult([])  # No results for issueFunction
            if "customfield_10014" in jql:
                # Return results for customfield query
                return MockSearchResult(
                    [
                        JiraIssue(key="CHILD-1", summary="Child 1"),
                        JiraIssue(key="CHILD-2", summary="Child 2"),
                    ]
                )
            msg = f"Unexpected JQL query as {jql}"
            raise KeyError(msg)

        epics_mixin.search_issues = MagicMock(side_effect=search_side_effect)

        # Call the method with start parameter
        result = epics_mixin.get_epic_issues("EPIC-123", start=3, limit=10)

        # Verify we got results from the second query
        assert len(result) == 2
        assert result[0].key == "CHILD-1"
        assert result[1].key == "CHILD-2"

        # Verify the start parameter was passed to search_issues
        assert epics_mixin.search_issues.call_count >= 2
        # Check last call (which should be the one that returned results)
        last_call_kwargs = epics_mixin.search_issues.call_args[1]
        assert last_call_kwargs.get("start") == 3
        assert last_call_kwargs.get("limit") == 10

    def test_get_epic_issues_api_error(self, epics_mixin: EpicsMixin):
        """Test get_epic_issues with API error."""
        # Setup mocks - simulate API error
        epics_mixin.jira.get_issue.side_effect = Exception("API error")

        # Call the method and expect an error
        with pytest.raises(
            Exception,
            match="Error getting epic issues: API error",
        ):
            epics_mixin.get_epic_issues("EPIC-123")

    @pytest.mark.parametrize(
        ("field_ids", "expected"),
        [
            ({"epic_name": "customfield_1"}, "customfield_1"),
            ({"Epic Name": "customfield_2"}, "customfield_2"),
            ({"other": "customfield_10011"}, "customfield_10011"),
            ({"Team Epic Name": "customfield_3"}, "customfield_3"),
            ({"summary": "summary"}, None),
        ],
    )
    def test_get_epic_name_field_id(self, epics_mixin: EpicsMixin, field_ids, expected):
        """Test Epic Name field lookup strategies."""
        assert epics_mixin._get_epic_name_field_id(field_ids) == expected

    @pytest.mark.parametrize(
        ("field_ids", "expected"),
        [
            ({"epic_color": "customfield_1"}, "customfield_1"),
            ({"epic_colour": "customfield_2"}, "customfield_2"),
            ({"other": "customfield_10012"}, "customfield_10012"),
            ({"Team Epic Colour": "customfield_3"}, "customfield_3"),
            ({"summary": "summary"}, None),
        ],
    )
    def test_get_epic_color_field_id(
        self, epics_mixin: EpicsMixin, field_ids, expected
    ):
        """Test Epic Color field lookup strategies."""
        assert epics_mixin._get_epic_color_field_id(field_ids) == expected

    @pytest.mark.parametrize(
        ("field_ids", "expected"),
        [
            ({"Epic Link": "customfield_1"}, "customfield_1"),
            ({"Team Epic Link": "customfield_2"}, "customfield_2"),
            ({"other": "customfield_10014"}, "customfield_10014"),
            ({"system.epic-link": "customfield_3"}, "customfield_3"),
        ],
    )
    def test_find_epic_link_field_known_strategies(
        self, epics_mixin: EpicsMixin, field_ids, expected
    ):
        """Test Epic Link field lookup uses known names and IDs."""
        assert epics_mixin._find_epic_link_field(field_ids) == expected

    def test_find_epic_link_field_from_linked_issue(self, epics_mixin: EpicsMixin):
        """Test Epic Link field inference from an issue linked to a sample Epic."""
        epics_mixin._find_sample_epic = MagicMock(return_value=[{"key": "EPIC-1"}])
        epics_mixin._find_issues_linked_to_epic = MagicMock(
            return_value=[
                {
                    "fields": {
                        "customfield_12345": "EPIC-1",
                        "summary": "Linked issue",
                    }
                }
            ]
        )

        result = epics_mixin._find_epic_link_field({})

        assert result == "customfield_12345"

    def test_find_epic_link_field_from_schema(self, epics_mixin: EpicsMixin):
        """Test Epic Link field lookup falls back to field schema inspection."""
        epics_mixin._find_sample_epic = MagicMock(return_value=[])
        epics_mixin.jira.get_all_fields.return_value = [
            {
                "id": "customfield_54321",
                "name": "Relationship",
                "schema": {"custom": "com.example:epic-relationship"},
            }
        ]

        result = epics_mixin._find_epic_link_field({})

        assert result == "customfield_54321"

    def test_find_epic_link_field_returns_none_after_errors(
        self, epics_mixin: EpicsMixin
    ):
        """Test Epic Link lookup returns None when discovery sources fail."""
        epics_mixin._find_sample_epic = MagicMock(
            side_effect=RuntimeError("search unavailable")
        )
        epics_mixin.jira.get_all_fields.side_effect = RuntimeError("fields unavailable")

        assert epics_mixin._find_epic_link_field({}) is None

    @pytest.mark.parametrize(
        ("response", "expected"),
        [
            ({"issues": [{"key": "EPIC-1"}]}, [{"key": "EPIC-1"}]),
            ({"issues": []}, []),
            ([], []),
        ],
    )
    def test_find_sample_epic(self, epics_mixin: EpicsMixin, response, expected):
        """Test sample Epic discovery handles success, empty, and malformed responses."""
        epics_mixin.jira.jql.return_value = response

        assert epics_mixin._find_sample_epic() == expected

    def test_find_issues_linked_to_epic_tries_queries(self, epics_mixin: EpicsMixin):
        """Test linked issue discovery retries alternate JQL forms."""
        epics_mixin.jira.jql.side_effect = [
            RuntimeError("unsupported JQL"),
            {"issues": []},
            {"issues": [{"key": "TEST-1"}]},
        ]

        result = epics_mixin._find_issues_linked_to_epic("EPIC-1")

        assert result == [{"key": "TEST-1"}]
        assert epics_mixin.jira.jql.call_count == 3

    def test_update_epic_fields_falls_back_to_individual_updates(
        self, epics_mixin: EpicsMixin
    ):
        """Test Epic field updates retry fields individually after bulk failure."""
        kwargs = {
            "__epic_name_field": "customfield_1",
            "__epic_name_value": "Epic name",
            "__epic_color_field": "customfield_2",
            "__epic_color_value": "blue",
        }
        epics_mixin.jira.update_issue.side_effect = [
            RuntimeError("bulk update failed"),
            None,
            RuntimeError("color unavailable"),
        ]

        result = epics_mixin.update_epic_fields("EPIC-1", kwargs)

        assert result == epics_mixin.get_issue.return_value
        assert epics_mixin.jira.update_issue.call_args_list == [
            call(
                "EPIC-1",
                update={
                    "fields": {
                        "customfield_1": "Epic name",
                        "customfield_2": "blue",
                    }
                },
            ),
            call(
                "EPIC-1",
                update={"fields": {"customfield_1": "Epic name"}},
            ),
            call("EPIC-1", update={"fields": {"customfield_2": "blue"}}),
        ]
        epics_mixin.get_issue.assert_called_once_with("EPIC-1")

    def test_update_epic_fields_uses_primary_update(self, epics_mixin: EpicsMixin):
        """Test Epic field updates use one request when the bulk update succeeds."""
        kwargs = {
            "__epic_name_field": "customfield_1",
            "__epic_name_value": "Epic name",
            "__epic_team_field": "customfield_3",
            "__epic_team_value": "Platform",
        }

        result = epics_mixin.update_epic_fields("EPIC-1", kwargs)

        assert result == epics_mixin.get_issue.return_value
        epics_mixin.jira.update_issue.assert_called_once_with(
            "EPIC-1",
            update={
                "fields": {
                    "customfield_1": "Epic name",
                    "customfield_3": "Platform",
                }
            },
        )

    def test_update_epic_fields_without_stored_fields(self, epics_mixin: EpicsMixin):
        """Test Epic updates without stored fields only refetch the issue."""
        result = epics_mixin.update_epic_fields("EPIC-1", {})

        assert result == epics_mixin.get_issue.return_value
        epics_mixin.jira.update_issue.assert_not_called()
        epics_mixin.get_issue.assert_called_once_with("EPIC-1")


class TestEpicFieldDynamicDetection:
    """Regression tests for dynamic Epic Link field discovery."""

    @pytest.fixture
    def epics_mixin(self, jira_fetcher: JiraFetcher) -> EpicsMixin:
        """Create an EpicsMixin instance with mocked dependencies."""
        return jira_fetcher

    def test_link_issue_to_epic_uses_dynamically_discovered_field(
        self, epics_mixin: EpicsMixin
    ):
        """Use the instance's Epic Link ID instead of a hardcoded fallback."""
        epics_mixin.jira.get_issue.side_effect = [
            {"key": "TEST-123"},
            {
                "key": "EPIC-456",
                "fields": {"issuetype": {"name": "Epic"}},
            },
            {  # verification read-back for the discovered Epic Link field
                "key": "TEST-123",
                "fields": {"customfield_10001": "EPIC-456"},
            },
        ]
        epics_mixin.jira.get_all_fields.return_value = [
            {
                "id": "customfield_10001",
                "name": "Epic Link",
                "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-link"},
            }
        ]
        epics_mixin.jira.jql.return_value = {"issues": []}
        epics_mixin.jira.update_issue.return_value = None
        epics_mixin.get_issue = MagicMock(
            return_value=JiraIssue(key="TEST-123", id="123456")
        )
        epics_mixin._field_ids_cache = None
        epics_mixin._field_name_to_id_map = None

        result = epics_mixin.link_issue_to_epic("TEST-123", "EPIC-456")

        # The discovered field is written first and its verified read-back ends
        # the search, so `parent` is never touched.
        assert epics_mixin.jira.update_issue.call_args_list == [
            call(
                issue_key="TEST-123",
                update={"fields": {"customfield_10001": "EPIC-456"}},
            ),
        ]
        epics_mixin.jira.get_all_fields.assert_called_once_with()
        epics_mixin.get_issue.assert_called_once_with("TEST-123")
        assert result == JiraIssue(
            key="TEST-123",
            id="123456",
        )


def _stub_issue() -> dict[str, Any]:
    """Return the payload the issue existence check reads."""
    return {"key": "TEST-123"}


def _stub_epic() -> dict[str, Any]:
    """Return the payload the epic type check reads."""
    return {"key": "EPIC-456", "fields": {"issuetype": {"name": "Epic"}}}


class TestEpicLinkVerification:
    """Regression tests for PROPX-398: a silent no-op is not a linked issue.

    Measured on a Jira Server/DC instance: link_issue_to_epic reported that
    CHSTC-1102 had been linked to epic CHSTC-574 while raw REST showed parent
    null, Epic Link (customfield_10006) null, updated identical to created and
    a changelog with zero entries. The PUT was accepted and wrote nothing, and
    the old code returned success on that acceptance alone.
    """

    @pytest.fixture
    def server_epics_mixin(self, mock_atlassian_jira: MagicMock) -> EpicsMixin:
        """Create an EpicsMixin bound to a Server/DC style instance.

        The shared jira_fetcher fixture points at https://test.atlassian.net,
        so is_cloud is True in every other epic test. The defect was measured
        on Server/DC, so these tests use a non-Cloud URL to keep that path
        exercised.

        Args:
            mock_atlassian_jira: Mocked atlassian.Jira client.

        Returns:
            EpicsMixin: Fetcher whose config reports is_cloud False.
        """
        config = JiraConfig(
            url="https://jira.example.com",
            auth_type="pat",
            personal_token="test-personal-token",
        )
        with patch("atlassian.Jira") as mock_jira_class:
            mock_jira_class.return_value = mock_atlassian_jira
            fetcher = JiraFetcher(config=config)

        fetcher.jira = mock_atlassian_jira
        assert fetcher.config.is_cloud is False
        fetcher.get_issue = MagicMock(
            return_value=JiraIssue(key="TEST-123", id="12345")
        )
        return fetcher

    def test_link_issue_to_epic_silent_noop_is_not_reported_as_success(
        self, server_epics_mixin: EpicsMixin
    ):
        """Raise instead of claiming success when no write actually landed.

        jira.update_issue is left with no side_effect on purpose: the MagicMock
        return models the measured HTTP 204 that writes nothing. Every existing
        test forced the failure path by making the update raise, which is
        precisely what the live instance does not do, so no call-args assertion
        can express this defect.
        """
        mixin = server_epics_mixin
        mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10006"}
        )
        mixin.jira.get_issue.side_effect = [
            _stub_issue(),
            _stub_epic(),
            # Epic Link read-back: the field is still empty.
            {"key": "TEST-123", "fields": {"customfield_10006": None}},
            # Parent read-back: also still empty.
            {"key": "TEST-123", "fields": {"parent": None}},
        ]

        with pytest.raises(ValueError, match="Could not link issue TEST-123"):
            mixin.link_issue_to_epic("TEST-123", "EPIC-456")

        # Both candidates were attempted and neither verified.
        assert mixin.jira.update_issue.call_count == 2
        # The caller never receives an issue model implying success.
        mixin.get_issue.assert_not_called()

    def test_link_verified_epic_link_write_returns_issue(
        self, server_epics_mixin: EpicsMixin
    ):
        """Report success on the discovered Epic Link field once verified.

        The single update is byte-identical to the jira_update_issue payload
        that was measured to work on the live instance.
        """
        mixin = server_epics_mixin
        mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10006"}
        )
        mixin.jira.get_issue.side_effect = [
            _stub_issue(),
            _stub_epic(),
            {"key": "TEST-123", "fields": {"customfield_10006": "EPIC-456"}},
        ]

        result = mixin.link_issue_to_epic("TEST-123", "EPIC-456")

        assert mixin.jira.update_issue.call_count == 1
        assert mixin.jira.update_issue.call_args_list == [
            call(
                issue_key="TEST-123",
                update={"fields": {"customfield_10006": "EPIC-456"}},
            )
        ]
        assert result is mixin.get_issue.return_value

    def test_link_falls_through_to_parent_when_epic_link_write_is_a_no_op(
        self, server_epics_mixin: EpicsMixin
    ):
        """Keep trying candidates after an accepted but ineffective write."""
        mixin = server_epics_mixin
        mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10006"}
        )
        mixin.jira.get_issue.side_effect = [
            _stub_issue(),
            _stub_epic(),
            # Epic Link was accepted but stored nothing.
            {"key": "TEST-123", "fields": {"customfield_10006": None}},
            # Parent did store the epic.
            {"key": "TEST-123", "fields": {"parent": {"key": "EPIC-456"}}},
        ]

        result = mixin.link_issue_to_epic("TEST-123", "EPIC-456")

        assert mixin.jira.update_issue.call_args_list == [
            call(
                issue_key="TEST-123",
                update={"fields": {"customfield_10006": "EPIC-456"}},
            ),
            call(
                issue_key="TEST-123",
                update={"fields": {"parent": {"key": "EPIC-456"}}},
            ),
        ]
        assert result is mixin.get_issue.return_value

    def test_link_verifies_with_a_narrow_field_list(
        self, server_epics_mixin: EpicsMixin
    ):
        """Verify with a one-field read and still return the full issue.

        Widening the final self.get_issue call instead of issuing a narrow
        verification read would skip the issue-model enrichment and silently
        drop comments from the tool response, so the two reads stay separate.
        """
        mixin = server_epics_mixin
        mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10006"}
        )
        mixin.jira.get_issue.side_effect = [
            _stub_issue(),
            _stub_epic(),
            {"key": "TEST-123", "fields": {"customfield_10006": "EPIC-456"}},
        ]

        mixin.link_issue_to_epic("TEST-123", "EPIC-456")

        assert mixin.jira.get_issue.call_args_list[2] == call(
            "TEST-123", fields="customfield_10006"
        )
        mixin.get_issue.assert_called_once_with("TEST-123")

    def test_link_never_writes_blind_custom_fields(
        self, server_epics_mixin: EpicsMixin
    ):
        """Write only fields this instance actually reported.

        The deleted fallback swept customfield_10014, _10008, _10000, _11703
        and a literal epic_link key. On the measured instance
        customfield_10008 is Epic Name and customfield_10000 is Flagged, so on
        an instance where either sits on the edit screen the sweep wrote the
        epic key into an unrelated field and then reported success.
        """
        mixin = server_epics_mixin
        mixin.get_field_ids_to_epic = MagicMock(return_value={})
        mixin.jira.get_issue.side_effect = [
            _stub_issue(),
            _stub_epic(),
            {"key": "TEST-123", "fields": {"parent": None}},
        ]

        with pytest.raises(ValueError, match="Could not link issue TEST-123"):
            mixin.link_issue_to_epic("TEST-123", "EPIC-456")

        written_fields = [
            field_id
            for mock_call in mixin.jira.update_issue.call_args_list
            for field_id in mock_call.kwargs["update"]["fields"]
        ]
        assert written_fields == ["parent"]
        mixin.jira.create_issue_link.assert_not_called()

    @pytest.mark.parametrize(
        ("read_back", "expected"),
        [
            pytest.param(
                {"key": "TEST-123", "fields": {"customfield_10006": "EPIC-456"}},
                True,
                id="string-value-matches",
            ),
            pytest.param(
                {"key": "TEST-123", "fields": {"customfield_10006": "OTHER-1"}},
                False,
                id="string-value-is-a-different-epic",
            ),
            pytest.param(
                {"key": "TEST-123", "fields": {"customfield_10006": None}},
                False,
                id="field-still-empty",
            ),
            pytest.param(
                {
                    "key": "TEST-123",
                    "fields": {"customfield_10006": {"key": "EPIC-456"}},
                },
                True,
                id="nested-issue-object-matches",
            ),
            pytest.param(
                {
                    "key": "TEST-123",
                    "fields": {"customfield_10006": {"key": "OTHER-1"}},
                },
                False,
                id="nested-issue-object-is-a-different-epic",
            ),
            pytest.param(
                {"key": "TEST-123", "fields": None},
                False,
                id="fields-is-null-as-measured-live",
            ),
            pytest.param(
                {"key": "TEST-123"},
                False,
                id="fields-key-absent",
            ),
            pytest.param(
                '{"key": "TEST-123", "fields": {"customfield_10006": "EPIC-456"}}',
                True,
                id="payload-is-a-json-string-as-measured-on-server-dc",
            ),
            pytest.param(
                '{"key": "TEST-123", "fields": {"customfield_10006": null}}',
                False,
                id="payload-is-a-json-string-with-an-empty-field",
            ),
            pytest.param("not a dict", False, id="payload-is-not-a-dict"),
            pytest.param(
                RuntimeError("field is hidden from this user"),
                False,
                id="read-back-raises",
            ),
        ],
    )
    def test_epic_link_written_reads_the_stored_value(
        self, server_epics_mixin: EpicsMixin, read_back: object, expected: bool
    ):
        """Only a stored value that names the epic counts as a written link.

        The ``fields-is-null-as-measured-live`` case is not hypothetical: on
        the measured Server/DC instance ``GET /issue/KEY?fields=parent`` with
        no parent set answers ``{"key": ..., "fields": null}``, so indexing
        into ``fields`` would raise instead of falling through to the next
        candidate.

        The JSON-string cases are not hypothetical either:
        atlassian-python-api returns the raw body as a string on Jira
        Server/DC when ``response.json()`` fails, which is why ``IssuesMixin``
        carries a ``json.loads`` workaround for its own read-back. Treating
        that payload as unverifiable turned a write that actually landed into
        a raised "could not link" error. A string that is not JSON at all
        (``payload-is-not-a-dict``) still counts as unverified.
        """
        mixin = server_epics_mixin
        if isinstance(read_back, Exception):
            mixin.jira.get_issue.side_effect = read_back
        else:
            mixin.jira.get_issue.side_effect = None
            mixin.jira.get_issue.return_value = read_back

        result = mixin._epic_link_written("TEST-123", "customfield_10006", "EPIC-456")

        assert result is expected
        mixin.jira.get_issue.assert_called_once_with(
            "TEST-123", fields="customfield_10006"
        )

    def test_link_does_not_poison_field_cache(self, server_epics_mixin: EpicsMixin):
        """Never invent a field-cache entry for a guessed epic link field.

        The deleted fallback appended {"id": <guess>, "name": "epic_link"} to
        _field_ids_cache on any accepted write, which made
        get_field_ids_to_epic return that guess for the life of the fetcher.
        """
        mixin = server_epics_mixin
        mixin._field_ids_cache = None
        mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10006"}
        )

        # Nothing verifies: the link fails and the cache stays untouched.
        mixin.jira.get_issue.side_effect = [
            _stub_issue(),
            _stub_epic(),
            {"key": "TEST-123", "fields": {"customfield_10006": None}},
            {"key": "TEST-123", "fields": {"parent": None}},
        ]
        with pytest.raises(ValueError, match="Could not link issue TEST-123"):
            mixin.link_issue_to_epic("TEST-123", "EPIC-456")
        assert mixin._field_ids_cache is None

        # A verified write must not cache anything either.
        mixin.jira.get_issue.side_effect = [
            _stub_issue(),
            _stub_epic(),
            {"key": "TEST-123", "fields": {"customfield_10006": "EPIC-456"}},
        ]
        mixin.link_issue_to_epic("TEST-123", "EPIC-456")
        assert mixin._field_ids_cache is None


class TestEpicLinkFieldCredibility:
    """Regression tests: a guessed epic link field must never be written.

    ``get_field_ids_to_epic`` maps every field definition's name onto its
    result dictionary, so a definition literally named ``epic_link`` shadows
    the genuine Epic Link discovery. ``get_epic_issues`` used to append exactly
    such a definition to ``_field_ids_cache`` whenever its last-resort JQL
    sweep matched, which made the deleted blind-custom-field hazard reachable
    through the verified write path: the epic key was written into the guessed
    field, read back, matched, and reported as a successful link. On the
    measured instance ``customfield_10008`` is Epic Name, a plain string field.
    """

    @pytest.fixture
    def server_epics_mixin(self, mock_atlassian_jira: MagicMock) -> EpicsMixin:
        """Create an EpicsMixin bound to a Server/DC style instance."""
        config = JiraConfig(
            url="https://jira.example.com",
            auth_type="pat",
            personal_token="test-personal-token",
        )
        with patch("atlassian.Jira") as mock_jira_class:
            mock_jira_class.return_value = mock_atlassian_jira
            fetcher = JiraFetcher(config=config)

        fetcher.jira = mock_atlassian_jira
        assert fetcher.config.is_cloud is False
        fetcher.get_issue = MagicMock(
            return_value=JiraIssue(key="TEST-123", id="12345")
        )
        return fetcher

    @staticmethod
    def _genuine_definitions() -> list[dict[str, Any]]:
        """Return the field definitions measured on the live instance."""
        return [
            {
                "id": "customfield_10006",
                "name": "Epic Link",
                "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-link"},
            },
            {
                "id": "customfield_10008",
                "name": "Epic Name",
                "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-label"},
            },
        ]

    def test_link_skips_a_poisoned_epic_link_cache_entry(
        self, server_epics_mixin: EpicsMixin
    ):
        """Never write the epic key into a fabricated epic link field.

        The cache contents here are exactly what get_epic_issues appended on a
        successful last-resort sweep. Field discovery still resolves epic_link
        to the guess -- that shadowing lives in FieldsMixin -- so the guard has
        to reject the candidate on the way in, before any write happens.
        """
        mixin = server_epics_mixin
        mixin._field_ids_cache = [
            *self._genuine_definitions(),
            # What get_epic_issues used to append, processed last and therefore
            # winning the generic name -> id mapping.
            {"id": "customfield_10008", "name": "epic_link"},
        ]
        mixin._field_name_to_id_map = None

        # The shadowing itself is unchanged: this documents why the guard is
        # needed rather than asserting the guard is unnecessary.
        assert mixin.get_field_ids_to_epic()["epic_link"] == "customfield_10008"

        mixin.jira.get_issue.side_effect = [
            _stub_issue(),
            _stub_epic(),
            {"key": "TEST-123", "fields": {"parent": None}},
        ]

        with pytest.raises(ValueError, match="Could not link issue TEST-123"):
            mixin.link_issue_to_epic("TEST-123", "EPIC-456")

        written_fields = [
            field_id
            for mock_call in mixin.jira.update_issue.call_args_list
            for field_id in mock_call.kwargs["update"]["fields"]
        ]
        assert written_fields == ["parent"]
        assert "customfield_10008" not in written_fields
        mixin.get_issue.assert_not_called()

    def test_link_uses_a_genuine_epic_link_field_from_the_same_cache(
        self, server_epics_mixin: EpicsMixin
    ):
        """Keep writing the real Epic Link field when it is discovered.

        Negative control for the guard: the same definitions without the
        fabricated entry must still produce the measured working write.
        """
        mixin = server_epics_mixin
        mixin._field_ids_cache = self._genuine_definitions()
        mixin._field_name_to_id_map = None
        mixin.jira.get_issue.side_effect = [
            _stub_issue(),
            _stub_epic(),
            {"key": "TEST-123", "fields": {"customfield_10006": "EPIC-456"}},
        ]

        result = mixin.link_issue_to_epic("TEST-123", "EPIC-456")

        assert mixin.jira.update_issue.call_args_list == [
            call(
                issue_key="TEST-123",
                update={"fields": {"customfield_10006": "EPIC-456"}},
            )
        ]
        assert result is mixin.get_issue.return_value

    def test_link_keeps_the_candidate_when_definitions_are_unavailable(
        self, server_epics_mixin: EpicsMixin
    ):
        """Do not narrow behaviour when no field definitions are cached.

        The cross-check reads the already-cached definitions and must never
        trigger a field fetch of its own, so an empty cache leaves the
        discovered candidate in place and the stored-value read-back decides
        it.
        """
        mixin = server_epics_mixin
        mixin._field_ids_cache = None
        mixin._field_name_to_id_map = None
        mixin.jira.get_all_fields.side_effect = Exception("field list forbidden")
        mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10006"}
        )
        mixin.jira.get_issue.side_effect = [
            _stub_issue(),
            _stub_epic(),
            {"key": "TEST-123", "fields": {"customfield_10006": "EPIC-456"}},
        ]

        result = mixin.link_issue_to_epic("TEST-123", "EPIC-456")

        assert mixin.jira.update_issue.call_args_list == [
            call(
                issue_key="TEST-123",
                update={"fields": {"customfield_10006": "EPIC-456"}},
            )
        ]
        assert result is mixin.get_issue.return_value
        mixin.jira.get_all_fields.assert_not_called()

    @pytest.mark.parametrize(
        ("definition", "expected"),
        [
            pytest.param(
                {
                    "id": "customfield_10006",
                    "name": "Epic Link",
                    "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-link"},
                },
                True,
                id="server-dc-epic-link",
            ),
            pytest.param(
                {
                    "id": "customfield_11703",
                    "name": "Epik-Verknuepfung",
                    "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-link"},
                },
                True,
                id="localized-name-with-the-plugin-schema",
            ),
            pytest.param(
                {"id": "customfield_10014", "name": "Anything"},
                True,
                id="cloud-epic-link-field-id",
            ),
            pytest.param(
                {"id": "customfield_10100", "name": "Epic Link"},
                True,
                id="epic-link-name-without-a-schema",
            ),
            pytest.param(
                {"id": "customfield_10008", "name": "epic_link"},
                False,
                id="fabricated-cache-entry",
            ),
            pytest.param(
                {
                    "id": "customfield_10008",
                    "name": "Epic Name",
                    "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-label"},
                },
                False,
                id="epic-name-is-not-epic-link",
            ),
            pytest.param(
                {"id": "customfield_10000", "name": "Flagged"},
                False,
                id="unrelated-field",
            ),
            pytest.param("not a definition", False, id="not-a-dict"),
        ],
    )
    def test_is_epic_link_field_definition(
        self, server_epics_mixin: EpicsMixin, definition: object, expected: bool
    ):
        """Accept a field for the same reasons field discovery accepts it.

        The fabricated entry is rejected precisely because its name uses an
        underscore: no Jira instance names the Epic Link field ``epic_link``,
        that string is this codebase's own internal key.
        """
        assert server_epics_mixin._is_epic_link_field_definition(definition) is expected

    def test_get_epic_issues_does_not_cache_a_guessed_epic_link_field(
        self, server_epics_mixin: EpicsMixin
    ):
        """A JQL match is not a field discovery, so it must not be cached.

        get_epic_issues' last-resort sweep appended a fabricated
        {"id": <guess>, "name": "epic_link"} definition to _field_ids_cache,
        which link_issue_to_epic then consumed as its first write candidate.
        """
        mixin = server_epics_mixin
        mixin._field_ids_cache = None
        mixin._field_name_to_id_map = None
        mixin.jira.get_issue.return_value = {
            "key": "EPIC-123",
            "fields": {"issuetype": {"name": "Epic"}},
        }
        mixin.get_field_ids_to_epic = MagicMock(return_value={})
        mixin._find_epic_link_field = MagicMock(return_value=None)

        class _Result:
            def __init__(self, issues: list[JiraIssue]):
                self.issues = issues

            def __bool__(self) -> bool:
                return bool(self.issues)

        def search_side_effect(jql: str, **kwargs: Any) -> _Result:
            if "customfield_10014" in jql:
                return _Result([JiraIssue(key="CHILD-1", summary="Child 1")])
            return _Result([])

        mixin.search_issues = MagicMock(side_effect=search_side_effect)

        result = mixin.get_epic_issues("EPIC-123")

        # The sweep still answers the query...
        assert [issue.key for issue in result] == ["CHILD-1"]
        # ...but it leaves no fabricated field definition behind.
        assert mixin._field_ids_cache is None


class TestEpicLinkVerificationTransport:
    """Regression tests for the string payload Jira Server/DC can return."""

    @pytest.fixture
    def server_epics_mixin(self, mock_atlassian_jira: MagicMock) -> EpicsMixin:
        """Create an EpicsMixin bound to a Server/DC style instance."""
        config = JiraConfig(
            url="https://jira.example.com",
            auth_type="pat",
            personal_token="test-personal-token",
        )
        with patch("atlassian.Jira") as mock_jira_class:
            mock_jira_class.return_value = mock_atlassian_jira
            fetcher = JiraFetcher(config=config)

        fetcher.jira = mock_atlassian_jira
        fetcher.get_issue = MagicMock(
            return_value=JiraIssue(key="TEST-123", id="12345")
        )
        return fetcher

    def test_link_succeeds_when_the_read_back_arrives_as_a_string(
        self, server_epics_mixin: EpicsMixin
    ):
        """A landed write must not be reported as a failure by a transport quirk.

        atlassian-python-api returns the raw body as a string on Jira
        Server/DC when response.json() fails -- the case IssuesMixin already
        works around with json.loads. Rejecting that payload made
        jira_link_to_epic raise for an epic link that was actually stored.
        """
        mixin = server_epics_mixin
        mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10006"}
        )
        mixin.jira.get_issue.side_effect = [
            _stub_issue(),
            _stub_epic(),
            json.dumps(
                {"key": "TEST-123", "fields": {"customfield_10006": "EPIC-456"}}
            ),
        ]

        result = mixin.link_issue_to_epic("TEST-123", "EPIC-456")

        assert mixin.jira.update_issue.call_args_list == [
            call(
                issue_key="TEST-123",
                update={"fields": {"customfield_10006": "EPIC-456"}},
            )
        ]
        assert result is mixin.get_issue.return_value

    def test_link_still_fails_on_an_unparseable_string_payload(
        self, server_epics_mixin: EpicsMixin
    ):
        """An unreadable read-back stays a loud failure, not a silent success."""
        mixin = server_epics_mixin
        mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10006"}
        )
        mixin.jira.get_issue.side_effect = [
            _stub_issue(),
            _stub_epic(),
            "<html>gateway timeout</html>",
            "<html>gateway timeout</html>",
        ]

        with pytest.raises(ValueError, match="Could not link issue TEST-123"):
            mixin.link_issue_to_epic("TEST-123", "EPIC-456")
        mixin.get_issue.assert_not_called()
