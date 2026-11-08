import csv
import io
import zipfile

import pytest
from django.urls import reverse

from research.export import (
    export_codebook_csv,
    export_csv,
    export_rows,
    export_zip,
)
from research.lens import Lens
from research.models import SetItem
from tests.factories import (
    CodeFactory,
    CodingFactory,
    MeetingPageFactory,
    MemoFactory,
    MuniFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


def _project_with_items():
    project = ProjectFactory()
    muni = MuniFactory(subdomain="berkeley", name="Berkeley", state="CA")
    document = MeetingPageFactory(
        document__municipality=muni,
        document__document_type="agenda",
        document__meeting_name="CityCouncil",
        page_number=3,
        text="The council approved the housing plan.",
    )
    result_set = ResultSetFactory(project=project, name="Snapshot")
    item = SetItemFactory(
        result_set=result_set,
        page=document,
        state="CA",
        document_type="agenda",
        disposition="r",
    )
    return project, result_set, item


@pytest.mark.django_db
class TestExportRows:
    def test_columns_and_values(self):
        project, result_set, item = _project_with_items()
        code = CodeFactory(project=project, name="Housing")
        CodingFactory(project=project, code=code, page=item.page)
        MemoFactory(project=project, page=item.page, body="Worth revisiting")

        rows = list(export_rows(project, result_set, Lens()))

        assert len(rows) == 1
        row = rows[0]
        assert list(row.keys()) == [
            "page_id",
            "civic_band_url",
            "municipality",
            "state",
            "meeting_name",
            "meeting_date",
            "document_type",
            "page_number",
            "disposition",
            "ignore_reason",
            "codes",
            "memo",
            "snippet",
        ]
        assert row["page_id"] == item.page_id
        assert row["civic_band_url"] == (
            "https://berkeley.civic.band/meetings/agendas/" + item.page_id
        )
        assert row["municipality"] == "Berkeley"
        assert row["state"] == "CA"
        assert row["meeting_name"] == "CityCouncil"
        assert row["document_type"] == "agenda"
        assert row["page_number"] == 3
        assert row["disposition"] == "Relevant"
        assert row["ignore_reason"] == ""
        assert row["codes"] == "Housing"
        assert row["memo"] == "Worth revisiting"
        assert "housing plan" in row["snippet"]

    def test_uses_item_denormalized_fields_when_page_is_missing(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        SetItem.objects.create(
            result_set=result_set,
            page_id="missing-page",
            state="OR",
            document_type="minutes",
        )

        rows = list(export_rows(project, result_set, Lens()))

        assert len(rows) == 1
        assert rows[0]["page_id"] == "missing-page"
        assert rows[0]["state"] == "OR"
        assert rows[0]["document_type"] == "minutes"
        assert rows[0]["civic_band_url"] == ""


@pytest.mark.django_db
class TestExportCsv:
    def test_writes_the_header_and_rows(self):
        project, result_set, _item = _project_with_items()

        lines = list(export_csv(project, result_set, Lens()))
        parsed = list(csv.reader(io.StringIO("".join(lines))))

        assert parsed[0][0] == "page_id"
        assert parsed[1][0] == _item.page_id

    def test_codebook_csv_includes_definitions_and_counts(self):
        project = ProjectFactory()
        code = CodeFactory(project=project, name="Housing", definition="Housing policy")
        CodingFactory(project=project, code=code, page=MeetingPageFactory())

        lines = list(export_codebook_csv(project))
        parsed = list(csv.reader(io.StringIO("".join(lines))))

        assert parsed[0] == [
            "code",
            "parent",
            "definition",
            "apply_when",
            "do_not_apply_when",
            "coding_count",
        ]
        assert parsed[1][0] == "Housing"
        assert parsed[1][2] == "Housing policy"
        assert parsed[1][5] == "1"


@pytest.mark.django_db
class TestExportZip:
    def test_zip_contains_csv_codebook_and_methods(self):
        project, result_set, _item = _project_with_items()

        payload = export_zip(project, result_set, Lens())
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            assert names == {"export.csv", "codebook.csv", "methods.md"}
            assert "page_id" in archive.read("export.csv").decode()
            assert "How we did this" in archive.read("methods.md").decode()


@pytest.mark.django_db
class TestExportView:
    def test_requires_login(self, client):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        response = client.get(
            reverse("research:set-export", args=[project.id, result_set.id])
        )
        assert response.status_code == 302

    def test_cannot_export_another_users_set(self, client):
        user = UserFactory()
        client.force_login(user)
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        response = client.get(
            reverse("research:set-export", args=[project.id, result_set.id])
        )
        assert response.status_code == 404

    def test_streams_a_zip_for_small_lenses(self, client):
        user = UserFactory()
        client.force_login(user)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=MeetingPageFactory())

        response = client.get(
            reverse("research:set-export", args=[project.id, result_set.id])
        )

        assert response.status_code == 200
        assert response["Content-Type"] == "application/zip"
        assert "attachment" in response["Content-Disposition"]
        payload = b"".join(response.streaming_content)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            assert "export.csv" in archive.namelist()

    def test_queues_a_job_for_large_lenses(self, client, settings):
        settings.RESEARCH_INLINE_EXPORT_MAX_ROWS = 1
        user = UserFactory()
        client.force_login(user)
        project = ProjectFactory(owner=user)
        result_set = ResultSetFactory(project=project)
        SetItemFactory(result_set=result_set, page=MeetingPageFactory())
        SetItemFactory(result_set=result_set, page=MeetingPageFactory())

        response = client.get(
            reverse("research:set-export", args=[project.id, result_set.id])
        )

        assert response.status_code == 202
        assert "Export queued" in response.content.decode()
        assert "job=" in response.content.decode()
