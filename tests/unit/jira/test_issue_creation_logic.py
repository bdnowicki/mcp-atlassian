from unittest.mock import MagicMock, patch

import pytest

from mcp_atlassian.jira.issues import IssuesMixin
from mcp_atlassian.jira.protocols import (
    AttachmentsOperationsProto,
    EpicOperationsProto,
    FieldsOperationsProto,
    IssueOperationsProto,
    ProjectsOperationsProto,
    UsersOperationsProto,
)


class ConcreteIssuesMixin(
    IssuesMixin,
    AttachmentsOperationsProto,
    EpicOperationsProto,
    FieldsOperationsProto,
    IssueOperationsProto,
    ProjectsOperationsProto,
    UsersOperationsProto,
):
    def _generate_field_map(self):
        pass

    def _get_account_id(self, user_identifier):
        pass

    def _try_discover_fields_from_existing_epic(self):
        pass

    def get_field_by_id(self, field_id):
        pass

    def get_field_ids_to_epic(self):
        pass

    def get_project_issue_types(self, project_key):
        pass

    def get_create_fields(self, project_key, issue_type_id):
        pass

    def get_required_fields(self, project_key, issue_type_name):
        pass

    def get_issue_forms(self, issue_key):
        pass

    def get_form_details(self, issue_key, form_id):
        pass

    def prepare_epic_fields(self, fields, summary, kwargs, project_key):
        pass

    def update_epic_fields(self, issue_key, epic_fields):
        pass

    def upload_attachments(self, issue_key, attachment_paths):
        pass

    def _format_field_value_for_write(self, field_id, value, field_definition):
        pass


@pytest.fixture
def issues_mixin():
    """Fixture to create an instance of IssuesMixin with a mocked jira client."""
    with patch("mcp_atlassian.jira.config.JiraConfig.from_env") as mock_from_env:
        mock_config = MagicMock()
        mock_config.is_cloud = True
        mock_config.url = "https://test.atlassian.net"
        mock_config.ssl_verify = True
        mock_from_env.return_value = mock_config

        mixin = ConcreteIssuesMixin()
        mixin.jira = MagicMock()
        mixin.config = mock_config
        # Mock methods that are not part of the mixin but are called by it
        mixin.get_project_issue_types = MagicMock()
        mixin.get_create_fields = MagicMock(return_value=[])
        mixin._markdown_to_jira = MagicMock(side_effect=lambda x: x)
        mixin._get_account_id = MagicMock(return_value="account_id_123")
        mixin._add_assignee_to_fields = MagicMock()
        mixin._prepare_epic_fields = MagicMock()
        mixin._prepare_parent_fields = MagicMock()
        mixin._process_additional_fields = MagicMock()
        mixin._handle_create_issue_error = MagicMock()

        # Mock the final call to get the created issue
        created_issue_response = {
            "key": "PROJ-123",
            "fields": {"summary": "Test Summary"},
        }
        mixin.jira.get_issue.return_value = created_issue_response

        return mixin


def test_create_issue_uses_epic_id_when_found(issues_mixin):
    """Verify create_issue uses the issue type ID for 'Epic' when found."""
    issues_mixin.get_project_issue_types.return_value = [
        {"id": "10000", "name": "Story", "subtask": False},
        {"id": "10001", "name": "Epic", "subtask": False},
    ]
    issues_mixin.jira.create_issue.return_value = {"key": "PROJ-1"}

    issues_mixin.create_issue(
        project_key="PROJ",
        summary="New Epic",
        issue_type="Epic",
    )

    call_args = issues_mixin.jira.create_issue.call_args
    assert call_args is not None
    fields = call_args[1]["fields"]
    assert "id" in fields["issuetype"].keys()
    assert "name" in fields["issuetype"].keys()
    assert fields["issuetype"]["id"] == "10001"


def test_create_issue_uses_subtask_id_when_found(issues_mixin):
    """Verify create_issue uses the issue type ID for 'Subtask' when found."""
    issues_mixin.get_project_issue_types.return_value = [
        {"id": "10002", "name": "Sub-task", "subtask": True},
    ]
    issues_mixin.jira.create_issue.return_value = {"key": "PROJ-2"}

    issues_mixin.create_issue(
        project_key="PROJ",
        summary="New Subtask",
        issue_type="Subtask",
        parent="PROJ-1",
    )

    call_args = issues_mixin.jira.create_issue.call_args
    assert call_args is not None
    fields = call_args[1]["fields"]
    assert "id" in fields["issuetype"].keys()
    assert "name" in fields["issuetype"].keys()
    assert fields["issuetype"]["id"] == "10002"


def test_create_issue_falls_back_to_name_if_id_not_found(issues_mixin):
    """Verify create_issue falls back to name if a type ID is not found."""
    issues_mixin.get_project_issue_types.return_value = [
        {"id": "10000", "name": "Story", "subtask": False},
    ]
    issues_mixin.jira.create_issue.return_value = {"key": "PROJ-3"}

    issues_mixin.create_issue(
        project_key="PROJ",
        summary="New Epic with no ID",
        issue_type="Epic",
    )

    call_args = issues_mixin.jira.create_issue.call_args
    assert call_args is not None
    fields = call_args[1]["fields"]
    assert fields["issuetype"] == {"name": "Epic"}


def test_find_epic_issue_type_id_returns_id(issues_mixin):
    """Verify _find_epic_issue_type_id returns the correct ID."""
    issues_mixin.get_project_issue_types.return_value = [
        {"id": "10001", "name": "Epic", "subtask": False},
    ]

    epic_id = issues_mixin._find_epic_issue_type_id("PROJ")
    assert epic_id == "10001"


def test_find_epic_issue_type_id_prefers_exact_match(issues_mixin):
    """Verify _find_epic_issue_type_id prefers exact 'Epic' over substring match."""
    issues_mixin.get_project_issue_types.return_value = [
        {"id": "10099", "name": "Program Epic", "subtask": False},
        {"id": "10001", "name": "Epic", "subtask": False},
    ]

    epic_id = issues_mixin._find_epic_issue_type_id("PROJ")
    assert epic_id == "10001"


def test_find_epic_issue_type_id_uses_epic_name_schema(issues_mixin):
    """Identify a localized Epic from its project-scoped Epic Name field."""
    issues_mixin.get_project_issue_types.return_value = [
        {"id": "10099", "name": "Team Epic", "subtask": False},
        {"id": "10001", "name": "Initiative", "subtask": False},
    ]
    issues_mixin.get_create_fields.side_effect = lambda _project, type_id: (
        [
            {
                "fieldId": "customfield_10103",
                "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-label"},
            }
        ]
        if type_id == "10001"
        else []
    )

    epic_id = issues_mixin._find_epic_issue_type_id("PROJ")

    assert epic_id == "10001"


def test_find_epic_issue_type_id_returns_none_if_not_found(issues_mixin):
    """Verify _find_epic_issue_type_id returns None when no Epic type exists."""
    issues_mixin.get_project_issue_types.return_value = [
        {"id": "10000", "name": "Story", "subtask": False},
    ]

    epic_id = issues_mixin._find_epic_issue_type_id("PROJ")
    assert epic_id is None


def test_find_subtask_issue_type_id_returns_id(issues_mixin):
    """Verify _find_subtask_issue_type_id returns the correct ID."""
    issues_mixin.get_project_issue_types.return_value = [
        {"id": "10002", "name": "Sub-task", "subtask": True},
    ]

    subtask_id = issues_mixin._find_subtask_issue_type_id("PROJ")
    assert subtask_id == "10002"


def test_find_subtask_issue_type_id_prefers_subtask_name_match(issues_mixin):
    """Verify Sub-task is preferred over other subtask-capable types."""
    issues_mixin.get_project_issue_types.return_value = [
        {"id": "24", "name": "Action Item", "subtask": True},
        {"id": "5", "name": "Sub-task", "subtask": True},
    ]

    subtask_id = issues_mixin._find_subtask_issue_type_id("PROJ")
    assert subtask_id == "5"


def test_find_subtask_issue_type_id_falls_back_to_first_subtask(issues_mixin):
    """Verify the first subtask-capable type is used as a fallback."""
    issues_mixin.get_project_issue_types.return_value = [
        {"id": "24", "name": "Action Item", "subtask": True},
        {"id": "25", "name": "Custom Child", "subtask": True},
    ]

    subtask_id = issues_mixin._find_subtask_issue_type_id("PROJ")
    assert subtask_id == "24"


def test_find_subtask_issue_type_id_returns_none_if_not_found(issues_mixin):
    """Verify _find_subtask_issue_type_id returns None when no Sub-task type exists."""
    issues_mixin.get_project_issue_types.return_value = [
        {"id": "10000", "name": "Story", "subtask": False},
    ]

    subtask_id = issues_mixin._find_subtask_issue_type_id("PROJ")
    assert subtask_id is None


# --- Epic link alias tests ---


class TestPrepareEpicLinkFields:
    """Tests for _prepare_epic_link_fields method."""

    @pytest.mark.parametrize(
        "alias",
        ["epicKey", "epic_link", "epicLink", "Epic Link"],
        ids=["epicKey", "epic_link", "epicLink", "Epic_Link"],
    )
    def test_alias_resolves_to_custom_field(self, issues_mixin, alias):
        """Epic link alias should resolve to the discovered custom field ID."""
        issues_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10014"}
        )
        fields: dict = {}
        kwargs = {alias: "EPIC-1"}

        issues_mixin._prepare_epic_link_fields(fields, kwargs)

        assert fields["customfield_10014"] == "EPIC-1"
        assert alias not in kwargs  # consumed from kwargs

    def test_cloud_fallback_to_parent_when_no_epic_field(self, issues_mixin):
        """On Cloud, if no epic link field discovered, fall back to parent."""
        issues_mixin.config.is_cloud = True
        issues_mixin.get_field_ids_to_epic = MagicMock(return_value={})
        fields: dict = {}
        kwargs = {"epicKey": "EPIC-1"}

        issues_mixin._prepare_epic_link_fields(fields, kwargs)

        assert fields["parent"] == {"key": "EPIC-1"}

    def test_server_unresolved_alias_raises_instead_of_no_op(self, issues_mixin):
        """On Server/DC with no epic link field, raise rather than write nothing.

        Replaces the previous contract, which asserted only that ``parent``
        was not set. PROPX-398: leaving *fields* untouched made ``update_issue``
        skip its PUT entirely and still return the issue as updated, i.e. the
        same silent no-op the ticket was opened about on
        ``link_issue_to_epic``. ``parent`` is still not a Server/DC fallback --
        Jira Server/DC has no parent-based epic link -- so the branch has to
        fail instead of guessing.
        """
        issues_mixin.config.is_cloud = False
        issues_mixin.get_field_ids_to_epic = MagicMock(return_value={})
        fields: dict = {}
        kwargs = {"epicKey": "EPIC-1"}

        with pytest.raises(ValueError, match="Could not resolve epic link alias"):
            issues_mixin._prepare_epic_link_fields(fields, kwargs)

        assert "parent" not in fields
        assert "customfield_10014" not in fields
        assert fields == {}

    def test_server_field_discovery_failure_raises(self, issues_mixin):
        """A failed field lookup is not an excuse to write nothing quietly."""
        issues_mixin.config.is_cloud = False
        issues_mixin.get_field_ids_to_epic = MagicMock(
            side_effect=RuntimeError("fields unavailable")
        )
        fields: dict = {}

        with pytest.raises(ValueError, match="Could not resolve epic link alias"):
            issues_mixin._prepare_epic_link_fields(fields, {"epicKey": "EPIC-1"})

        assert fields == {}

    def test_resolved_alias_reports_the_field_to_read_back(self, issues_mixin):
        """The resolved field is returned so the caller can verify the write.

        Jira answers ``204 No Content`` to a PUT that stores nothing, so
        preparing the field is not evidence of a write; the caller reads this
        field back afterwards (see IssuesMixin._verify_epic_link_written).
        """
        issues_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10014"}
        )
        fields: dict = {}

        pending = issues_mixin._prepare_epic_link_fields(fields, {"epicKey": "EPIC-1"})

        assert pending == ("customfield_10014", "EPIC-1")

    def test_cloud_parent_fallback_reports_parent_to_read_back(self, issues_mixin):
        """The Cloud parent fallback is verified like any other candidate."""
        issues_mixin.config.is_cloud = True
        issues_mixin.get_field_ids_to_epic = MagicMock(return_value={})
        fields: dict = {}

        pending = issues_mixin._prepare_epic_link_fields(fields, {"epicKey": "EPIC-1"})

        assert pending == ("parent", "EPIC-1")

    def test_explicit_parent_conflict_reports_nothing_to_verify(
        self, issues_mixin, caplog
    ):
        """An explicit parent wins, and nothing was set from the alias.

        Nothing to verify here, because nothing was written from the alias --
        but the ignored alias is now logged as a warning instead of vanishing.
        """
        issues_mixin.config.is_cloud = True
        issues_mixin.get_field_ids_to_epic = MagicMock(return_value={})
        fields: dict = {"parent": {"key": "EXISTING-1"}}

        with caplog.at_level("WARNING", logger="mcp-jira"):
            pending = issues_mixin._prepare_epic_link_fields(
                fields, {"epicKey": "EPIC-1"}
            )

        assert pending is None
        assert fields["parent"] == {"key": "EXISTING-1"}
        assert "Ignoring epic link alias" in caplog.text

    def test_no_alias_reports_nothing_to_verify(self, issues_mixin):
        """NEGATIVE CONTROL: an update without an epic alias verifies nothing."""
        assert issues_mixin._prepare_epic_link_fields({}, {"priority": "High"}) is None

    def test_fallback_skipped_when_parent_already_set(self, issues_mixin):
        """If parent is already in fields, Cloud fallback should not override it."""
        issues_mixin.config.is_cloud = True
        issues_mixin.get_field_ids_to_epic = MagicMock(return_value={})
        fields: dict = {"parent": {"key": "EXISTING-1"}}
        kwargs = {"epicKey": "EPIC-1"}

        issues_mixin._prepare_epic_link_fields(fields, kwargs)

        assert fields["parent"] == {"key": "EXISTING-1"}

    def test_alias_consumed_from_kwargs(self, issues_mixin):
        """Epic link alias must be removed from kwargs to prevent double processing."""
        issues_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10014"}
        )
        fields: dict = {}
        kwargs = {"epicKey": "EPIC-1", "priority": "High"}

        issues_mixin._prepare_epic_link_fields(fields, kwargs)

        assert "epicKey" not in kwargs
        assert kwargs["priority"] == "High"  # other kwargs untouched

    def test_no_op_when_no_alias_present(self, issues_mixin):
        """No changes when no epic link alias is in kwargs."""
        fields: dict = {}
        kwargs = {"priority": "High"}

        issues_mixin._prepare_epic_link_fields(fields, kwargs)

        assert fields == {}
        assert kwargs == {"priority": "High"}

    def test_empty_value_is_ignored(self, issues_mixin):
        """Empty string epic key should be treated as no-op."""
        fields: dict = {}
        kwargs = {"epicKey": ""}

        issues_mixin._prepare_epic_link_fields(fields, kwargs)

        assert fields == {}

    def test_none_value_is_ignored(self, issues_mixin):
        """None epic key should be treated as no-op."""
        fields: dict = {}
        kwargs = {"epicKey": None}

        issues_mixin._prepare_epic_link_fields(fields, kwargs)

        assert fields == {}


class TestCreateIssueEpicLink:
    """Tests for epic link alias resolution in create_issue."""

    def test_create_issue_resolves_epic_alias(self, issues_mixin):
        """create_issue should resolve epicKey alias to epic link custom field."""
        issues_mixin.get_project_issue_types.return_value = [
            {"id": "10000", "name": "Story", "subtask": False},
        ]
        issues_mixin.jira.create_issue.return_value = {"key": "PROJ-1"}
        issues_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10014"}
        )

        issues_mixin.create_issue(
            project_key="PROJ",
            summary="Linked to epic",
            issue_type="Story",
            epicKey="EPIC-1",
        )

        # Check that create_issue was called and epicKey was resolved
        call_args = issues_mixin.jira.create_issue.call_args
        assert call_args is not None
        fields = call_args[1]["fields"]
        assert fields.get("customfield_10014") == "EPIC-1"

    def test_create_issue_epic_alias_not_in_additional_fields(self, issues_mixin):
        """epicKey alias should NOT be passed to _process_additional_fields."""
        issues_mixin.get_project_issue_types.return_value = [
            {"id": "10000", "name": "Story", "subtask": False},
        ]
        issues_mixin.jira.create_issue.return_value = {"key": "PROJ-1"}
        issues_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10014"}
        )

        issues_mixin.create_issue(
            project_key="PROJ",
            summary="Linked to epic",
            issue_type="Story",
            epicKey="EPIC-1",
        )

        # _process_additional_fields should not receive epicKey
        paf_call = issues_mixin._process_additional_fields.call_args
        if paf_call:
            kwargs_passed = paf_call[0][1]  # second arg is kwargs
            assert "epicKey" not in kwargs_passed

    def test_create_issue_explicit_parent_overrides_epic_fallback(self, issues_mixin):
        """When both epicKey and parent are passed, explicit parent wins."""
        issues_mixin.config.is_cloud = True
        issues_mixin.get_project_issue_types.return_value = [
            {"id": "10000", "name": "Story", "subtask": False},
        ]
        issues_mixin.jira.create_issue.return_value = {"key": "PROJ-1"}
        # No epic link field discovered → Cloud fallback would set parent
        issues_mixin.get_field_ids_to_epic = MagicMock(return_value={})
        # Restore _prepare_parent_fields to real implementation so it overwrites
        issues_mixin._prepare_parent_fields = (
            IssuesMixin._prepare_parent_fields.__get__(issues_mixin)
        )

        issues_mixin.create_issue(
            project_key="PROJ",
            summary="Story with both",
            issue_type="Story",
            epicKey="EPIC-1",
            parent="PARENT-1",
        )

        call_args = issues_mixin.jira.create_issue.call_args
        assert call_args is not None
        fields = call_args[1]["fields"]
        # Explicit parent should override Cloud fallback
        assert fields["parent"] == {"key": "PARENT-1"}


class TestCreateIssueUnresolvedEpicLink:
    """PROPX-398: creating an issue must not silently drop the epic link."""

    def test_create_issue_unresolved_epic_alias_raises_before_creating(
        self, issues_mixin
    ):
        """Fail before the POST rather than create an issue with no epic link.

        The alias is resolved while the create fields are built, so a
        Server/DC instance with no discoverable Epic Link field now reports
        the problem instead of creating an unlinked issue and returning it as
        though the link had been made. Nothing is created, so the caller can
        retry with the exact custom field ID named in the message.
        """
        issues_mixin.config.is_cloud = False
        issues_mixin.get_project_issue_types.return_value = [
            {"id": "10000", "name": "Story", "subtask": False},
        ]
        issues_mixin.get_field_ids_to_epic = MagicMock(return_value={})

        with pytest.raises(ValueError, match="Could not resolve epic link alias"):
            issues_mixin.create_issue(
                project_key="PROJ",
                summary="Linked to epic",
                issue_type="Story",
                epicKey="EPIC-1",
            )

        issues_mixin.jira.create_issue.assert_not_called()


class TestUpdateIssueParent:
    """Tests for parent handling in update_issue."""

    def test_update_issue_parent_string(self, issues_mixin):
        """update_issue should handle parent as a string key."""
        issues_mixin.get_field_ids_to_epic = MagicMock(return_value={})
        issues_mixin.jira.update_issue = MagicMock()
        issues_mixin.jira.get_issue.return_value = {
            "key": "PROJ-1",
            "fields": {"summary": "Test"},
        }

        issues_mixin.update_issue(issue_key="PROJ-1", parent="PROJ-2")

        call_args = issues_mixin.jira.update_issue.call_args
        assert call_args is not None
        update_fields = call_args[1]["update"]["fields"]
        assert update_fields["parent"] == {"key": "PROJ-2"}

    def test_update_issue_parent_dict(self, issues_mixin):
        """update_issue should handle parent as a dict with key."""
        issues_mixin.get_field_ids_to_epic = MagicMock(return_value={})
        issues_mixin.jira.update_issue = MagicMock()
        issues_mixin.jira.get_issue.return_value = {
            "key": "PROJ-1",
            "fields": {"summary": "Test"},
        }

        issues_mixin.update_issue(issue_key="PROJ-1", parent={"key": "PROJ-2"})

        call_args = issues_mixin.jira.update_issue.call_args
        assert call_args is not None
        update_fields = call_args[1]["update"]["fields"]
        assert update_fields["parent"] == {"key": "PROJ-2"}


class TestUpdateIssueEpicLink:
    """Tests for epic link alias resolution in update_issue."""

    def test_update_issue_resolves_epic_alias(self, issues_mixin):
        """update_issue should resolve epicKey alias to epic link custom field.

        The stored value is read back through ``EpicsMixin._epic_link_written``
        (PROPX-398), which this standalone IssuesMixin harness does not
        compose, so it is mocked here and asserted on: the alias resolution and
        the verification are one contract, and a write that is not verified
        must not be reported as done.
        """
        issues_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10014"}
        )
        issues_mixin.jira.update_issue = MagicMock()
        issues_mixin._epic_link_written = MagicMock(return_value=True)
        issues_mixin.jira.get_issue.return_value = {
            "key": "PROJ-1",
            "fields": {"summary": "Test"},
        }

        issues_mixin.update_issue(issue_key="PROJ-1", epicKey="EPIC-1")

        call_args = issues_mixin.jira.update_issue.call_args
        assert call_args is not None
        update_fields = call_args[1]["update"]["fields"]
        assert update_fields.get("customfield_10014") == "EPIC-1"
        issues_mixin._epic_link_written.assert_called_once_with(
            "PROJ-1", "customfield_10014", "EPIC-1"
        )

    def test_update_issue_unverified_epic_alias_raises(self, issues_mixin):
        """An epic link write Jira did not store must not report success.

        Measured on CHSTC-1102: the PUT answered 204, the field stayed null,
        ``updated`` was unchanged and the changelog stayed empty, yet the
        caller was told the link had been made.
        """
        issues_mixin.get_field_ids_to_epic = MagicMock(
            return_value={"epic_link": "customfield_10014"}
        )
        issues_mixin.jira.update_issue = MagicMock()
        issues_mixin._epic_link_written = MagicMock(return_value=False)
        issues_mixin.jira.get_issue.return_value = {
            "key": "PROJ-1",
            "fields": {"summary": "Test"},
        }

        with pytest.raises(ValueError, match="does not hold epic key EPIC-1"):
            issues_mixin.update_issue(issue_key="PROJ-1", epicKey="EPIC-1")
