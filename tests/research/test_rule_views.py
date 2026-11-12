from unittest.mock import patch

import pytest
from django.urls import reverse

from research.models import AuditEvent, IgnoreRule, ResultSet
from tests.factories import (
    IgnoreRuleFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


@pytest.mark.django_db
class TestRuleViews:
    def _project(self):
        user = UserFactory()
        return user, ProjectFactory(owner=user)

    def test_rule_list_requires_login(self, client):
        _, project = self._project()
        assert (
            client.get(reverse("research:rule-list", args=[project.id])).status_code
            == 302
        )

    def test_rule_list_owner_scoped(self, client):
        user, project = self._project()
        IgnoreRuleFactory(project=project, name="Mine")
        IgnoreRuleFactory(project=ProjectFactory(), name="Other")
        client.force_login(user)

        response = client.get(reverse("research:rule-list", args=[project.id]))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Mine" in content
        assert "Other" not in content

    def test_rule_list_shows_ignored_count_and_explanation(self, client):
        user, project = self._project()
        rule = IgnoreRuleFactory(project=project, name="Boilerplate")
        item = SetItemFactory(
            result_set=ResultSetFactory(project=project), disposition="i"
        )
        item.disposition_by_rule = rule
        item.save()
        client.force_login(user)

        response = client.get(reverse("research:rule-list", args=[project.id]))

        content = response.content.decode()
        assert "1 page currently ignored" in content
        assert "do not create a new set" in content

    def test_create_requires_login(self, client):
        _, project = self._project()
        assert (
            client.get(reverse("research:rule-create", args=[project.id])).status_code
            == 302
        )

    def test_create_prefills_from_item(self, client):
        user, project = self._project()
        item = SetItemFactory(result_set=ResultSetFactory(project=project))
        client.force_login(user)

        response = client.get(
            reverse("research:rule-create", args=[project.id]) + f"?page={item.id}"
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert f'value="{item.page.meeting_name}"' in content
        assert '<option value="agenda" selected>' in content

    def test_create_ignores_invalid_page_prefill(self, client):
        user, project = self._project()
        client.force_login(user)

        response = client.get(
            reverse("research:rule-create", args=[project.id]) + "?page=not-a-number"
        )

        assert response.status_code == 200

    def test_create_previews_without_saving(self, client):
        user, project = self._project()
        result_set = ResultSetFactory(project=project, name="Preview Set")
        SetItemFactory(result_set=result_set)
        client.force_login(user)

        response = client.post(
            reverse("research:rule-create", args=[project.id]),
            {"name": "CA", "states": "CA", "action": "preview"},
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Preview Set" in content
        assert "1 unreviewed page" in content
        assert not IgnoreRule.objects.filter(project=project).exists()

    def test_malformed_text_preview_returns_400_without_saving(self, client):
        user, project = self._project()
        client.force_login(user)

        with patch("research.views.validate_rule_predicate", return_value="bad syntax"):
            response = client.post(
                reverse("research:rule-create", args=[project.id]),
                {"name": "Housing", "text": "housing", "action": "preview"},
            )

        assert response.status_code == 400
        assert "bad syntax" in response.content.decode()
        assert not IgnoreRule.objects.filter(project=project).exists()

    def test_malformed_text_save_returns_400_without_saving(self, client):
        user, project = self._project()
        client.force_login(user)

        with patch("research.views.validate_rule_predicate", return_value="bad syntax"):
            response = client.post(
                reverse("research:rule-create", args=[project.id]),
                {"name": "Housing", "text": "housing", "action": "save"},
            )

        assert response.status_code == 400
        assert "bad syntax" in response.content.decode()
        assert not IgnoreRule.objects.filter(project=project).exists()

    def test_empty_predicate_save_returns_400_without_saving(self, client):
        user, project = self._project()
        client.force_login(user)

        response = client.post(
            reverse("research:rule-create", args=[project.id]),
            {"name": "Everything", "action": "save"},
        )

        assert response.status_code == 400
        assert "at least one condition" in response.content.decode()
        assert not IgnoreRule.objects.filter(project=project).exists()

    def test_too_long_rule_name_returns_400_without_saving(self, client):
        user, project = self._project()
        client.force_login(user)

        response = client.post(
            reverse("research:rule-create", args=[project.id]),
            {"name": "x" * 201, "states": "CA", "action": "save"},
        )

        assert response.status_code == 400
        assert not IgnoreRule.objects.filter(project=project).exists()

    def test_long_reason_is_truncated(self, client, settings):
        user, project = self._project()
        client.force_login(user)

        with patch("django_rq.get_queue"):
            response = client.post(
                reverse("research:rule-create", args=[project.id]),
                {
                    "name": "CA",
                    "states": "CA",
                    "reason": "x" * 600,
                    "action": "save",
                },
            )

        assert response.status_code == 302
        rule = IgnoreRule.objects.get(project=project)
        assert len(rule.reason) == 500

    def test_create_saves_and_enqueues(self, client, settings):
        user, project = self._project()
        client.force_login(user)

        with patch("django_rq.get_queue") as get_queue:
            response = client.post(
                reverse("research:rule-create", args=[project.id]),
                {
                    "name": "CA",
                    "states": "CA",
                    "reason": "Out of state",
                    "action": "save",
                },
            )

        assert response.status_code == 302
        rule = IgnoreRule.objects.get(project=project)
        assert rule.predicate["states"] == ["CA"]
        assert rule.reason == "Out of state"
        assert AuditEvent.objects.filter(project=project, verb="rule.created").exists()
        get_queue.assert_called_once_with(settings.RESEARCH_QUEUE)

    def test_toggle_off_enqueues_revert(self, client, settings):
        user, project = self._project()
        rule = IgnoreRuleFactory(project=project, is_active=True)
        client.force_login(user)

        with (
            patch("research.views.apply_rule_to_project_task") as apply_task,
            patch("research.views.revert_rule_task") as revert_task,
            patch("django_rq.get_queue") as get_queue,
        ):
            response = client.post(
                reverse("research:rule-toggle", args=[project.id, rule.id])
            )

        assert response.status_code == 302
        rule.refresh_from_db()
        assert rule.is_active is False
        assert AuditEvent.objects.filter(project=project, verb="rule.changed").exists()
        get_queue.assert_called_once_with(settings.RESEARCH_QUEUE)
        enqueued = get_queue.return_value.enqueue.call_args
        assert enqueued.args[0] is revert_task
        assert apply_task not in enqueued.args

    def test_toggle_on_enqueues_apply(self, client, settings):
        user, project = self._project()
        rule = IgnoreRuleFactory(project=project, is_active=False)
        client.force_login(user)

        with (
            patch("research.views.apply_rule_to_project_task") as apply_task,
            patch("research.views.revert_rule_task") as revert_task,
            patch("django_rq.get_queue") as get_queue,
        ):
            client.post(reverse("research:rule-toggle", args=[project.id, rule.id]))

        rule.refresh_from_db()
        assert rule.is_active is True
        get_queue.assert_called_once_with(settings.RESEARCH_QUEUE)
        enqueued = get_queue.return_value.enqueue.call_args
        assert enqueued.args[0] is apply_task
        assert revert_task not in enqueued.args

    def test_delete_reverts_and_deletes(self, client):
        user, project = self._project()
        item = SetItemFactory(
            result_set=ResultSetFactory(project=project), disposition="i"
        )
        rule = IgnoreRuleFactory(project=project)
        item.disposition_by_rule = rule
        item.save()
        client.force_login(user)

        response = client.post(
            reverse("research:rule-delete", args=[project.id, rule.id])
        )

        assert response.status_code == 302
        assert not IgnoreRule.objects.filter(id=rule.id).exists()
        item.refresh_from_db()
        assert item.disposition == "u"
        assert item.disposition_by_rule is None

    def test_delete_inactive_rule_reverts_owned_rows(self, client):
        user, project = self._project()
        item = SetItemFactory(
            result_set=ResultSetFactory(project=project), disposition="i"
        )
        rule = IgnoreRuleFactory(project=project, is_active=False)
        item.disposition_by_rule = rule
        item.save()
        client.force_login(user)

        response = client.post(
            reverse("research:rule-delete", args=[project.id, rule.id])
        )

        assert response.status_code == 302
        assert not IgnoreRule.objects.filter(id=rule.id).exists()
        item.refresh_from_db()
        assert item.disposition == "u"
        assert item.disposition_by_rule is None

    def test_item_card_has_ignore_like_this_and_datalist(self, client):
        user, project = self._project()
        result_set = ResultSetFactory(project=project, status=ResultSet.Status.READY)
        SetItemFactory(result_set=result_set)
        client.force_login(user)

        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
        )

        content = response.content.decode()
        assert "Ignore pages like this" in content
        assert 'list="ignore-reasons"' in content
        assert content.count('id="ignore-reasons"') == 1

    def test_set_detail_has_make_ignore_rule(self, client):
        user, project = self._project()
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set)
        client.force_login(user)

        response = client.get(
            reverse("research:set-detail", args=[project.id, result_set.id])
        )

        assert "Make ignore rule" in response.content.decode()


@pytest.mark.django_db
class TestRuleJobStatusViews:
    def _project(self):
        user = UserFactory()
        return user, ProjectFactory(owner=user)

    def test_create_redirects_to_status_and_marks_queued(self, client):
        user, project = self._project()
        client.force_login(user)

        with patch("django_rq.get_queue") as get_queue:
            get_queue.return_value.enqueue.return_value.id = "job-123"
            response = client.post(
                reverse("research:rule-create", args=[project.id]),
                {"name": "CA", "states": "CA", "action": "save"},
            )

        rule = IgnoreRule.objects.get(project=project)
        assert response.status_code == 302
        assert response["Location"] == reverse(
            "research:rule-status", args=[project.id, rule.id]
        )
        assert rule.last_job_action == "apply"
        assert rule.last_job_status == "queued"
        assert rule.last_job_id == "job-123"
        assert rule.last_job_error == ""
        assert rule.last_job_count is None
        assert rule.last_job_finished_at is None

    def test_create_enqueue_failure_marks_failed(self, client):
        user, project = self._project()
        client.force_login(user)

        with patch("django_rq.get_queue") as get_queue:
            get_queue.return_value.enqueue.side_effect = RuntimeError("redis down")
            response = client.post(
                reverse("research:rule-create", args=[project.id]),
                {"name": "CA", "states": "CA", "action": "save"},
            )

        rule = IgnoreRule.objects.get(project=project)
        assert response.status_code == 302
        assert rule.last_job_status == "failed"
        assert "redis down" in rule.last_job_error
        assert rule.last_job_finished_at is not None

    def test_toggle_off_redirects_and_marks_queued_revert(self, client):
        user, project = self._project()
        rule = IgnoreRuleFactory(project=project, is_active=True)
        client.force_login(user)

        with (
            patch("research.views.apply_rule_to_project_task"),
            patch("research.views.revert_rule_task"),
            patch("django_rq.get_queue") as get_queue,
        ):
            get_queue.return_value.enqueue.return_value.id = "job-revert"
            response = client.post(
                reverse("research:rule-toggle", args=[project.id, rule.id])
            )

        rule.refresh_from_db()
        assert response.status_code == 302
        assert response["Location"] == reverse(
            "research:rule-status", args=[project.id, rule.id]
        )
        assert rule.last_job_action == "revert"
        assert rule.last_job_status == "queued"
        assert rule.last_job_id == "job-revert"

    def test_toggle_on_redirects_and_marks_queued_apply(self, client):
        user, project = self._project()
        rule = IgnoreRuleFactory(project=project, is_active=False)
        client.force_login(user)

        with (
            patch("research.views.apply_rule_to_project_task"),
            patch("research.views.revert_rule_task"),
            patch("django_rq.get_queue") as get_queue,
        ):
            get_queue.return_value.enqueue.return_value.id = "job-apply"
            response = client.post(
                reverse("research:rule-toggle", args=[project.id, rule.id])
            )

        rule.refresh_from_db()
        assert response.status_code == 302
        assert response["Location"] == reverse(
            "research:rule-status", args=[project.id, rule.id]
        )
        assert rule.last_job_action == "apply"
        assert rule.last_job_status == "queued"
        assert rule.last_job_id == "job-apply"

    def test_status_page_requires_login(self, client):
        _, project = self._project()
        rule = IgnoreRuleFactory(project=project)

        response = client.get(
            reverse("research:rule-status", args=[project.id, rule.id])
        )

        assert response.status_code == 302

    def test_status_page_owner_scoped(self, client):
        user, project = self._project()
        rule = IgnoreRuleFactory(project=ProjectFactory())
        client.force_login(user)

        response = client.get(
            reverse("research:rule-status", args=[project.id, rule.id])
        )

        assert response.status_code == 404

    def test_status_page_shows_running(self, client):
        user, project = self._project()
        rule = IgnoreRuleFactory(
            project=project,
            last_job_action="apply",
            last_job_status="running",
        )
        client.force_login(user)

        response = client.get(
            reverse("research:rule-status", args=[project.id, rule.id])
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert "Applying rule" in content
        assert 'hx-trigger="every 2s"' in content

    def test_status_page_shows_success_count(self, client):
        user, project = self._project()
        rule = IgnoreRuleFactory(
            project=project,
            last_job_action="apply",
            last_job_status="succeeded",
            last_job_count=3,
        )
        client.force_login(user)

        response = client.get(
            reverse("research:rule-status", args=[project.id, rule.id])
        )

        content = response.content.decode()
        assert "3 pages ignored" in content
        assert 'hx-trigger="every 2s"' not in content

    def test_status_page_shows_failure(self, client):
        user, project = self._project()
        rule = IgnoreRuleFactory(
            project=project,
            last_job_action="apply",
            last_job_status="failed",
            last_job_error="boom",
        )
        client.force_login(user)

        response = client.get(
            reverse("research:rule-status", args=[project.id, rule.id])
        )

        content = response.content.decode()
        assert "Apply failed" in content
        assert "boom" in content
        assert 'hx-trigger="every 2s"' not in content

    def test_rule_list_polls_while_rule_pending(self, client):
        user, project = self._project()
        IgnoreRuleFactory(
            project=project,
            last_job_action="apply",
            last_job_status="queued",
        )
        client.force_login(user)

        response = client.get(reverse("research:rule-list", args=[project.id]))

        content = response.content.decode()
        assert "Applying" in content
        assert 'hx-trigger="every 2s"' in content
        assert 'hx-select="#rule-list"' in content

    def test_rule_list_does_not_poll_when_done(self, client):
        user, project = self._project()
        IgnoreRuleFactory(
            project=project,
            last_job_action="apply",
            last_job_status="succeeded",
            last_job_count=2,
        )
        client.force_login(user)

        response = client.get(reverse("research:rule-list", args=[project.id]))

        content = response.content.decode()
        assert "Applied" in content
        assert "2 pages" in content
        assert 'hx-trigger="every 2s"' not in content
