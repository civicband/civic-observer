import pytest

from research.codebook import (
    apply_code_to_lens,
    apply_code_to_pages,
    archive_code,
    codebook_tree,
    create_code,
    merge_codes,
    remove_code_from_lens,
    remove_code_from_pages,
    rename_code,
    set_exemplar,
)
from research.lens import Lens
from research.models import Code, Coding
from tests.factories import (
    CodeFactory,
    CodingFactory,
    MeetingPageFactory,
    ProjectFactory,
    ResultSetFactory,
    SetItemFactory,
    UserFactory,
)


def _project_with_page():
    project = ProjectFactory()
    page = MeetingPageFactory(text="The council approved the housing plan.")
    result_set = ResultSetFactory(project=project)
    SetItemFactory(result_set=result_set, page=page)
    return project, page, result_set


@pytest.mark.django_db
class TestCodebookTree:
    def test_returns_roots_with_children_and_counts(self):
        project = ProjectFactory()
        parent = CodeFactory(project=project, name="Housing")
        child = CodeFactory(project=project, name="Affordable housing", parent=parent)
        page = MeetingPageFactory()
        CodingFactory(project=project, code=parent, page=page)
        CodingFactory(project=project, code=child, page=MeetingPageFactory())

        tree = codebook_tree(project)

        assert [node["code"].name for node in tree] == ["Housing"]
        assert tree[0]["coding_count"] == 1
        assert [node["code"].name for node in tree[0]["children"]] == [
            "Affordable housing"
        ]
        assert tree[0]["children"][0]["coding_count"] == 1

    def test_excludes_archived_by_default_and_can_include_them(self):
        project = ProjectFactory()
        CodeFactory(project=project, name="Live")
        CodeFactory(project=project, name="Retired", is_archived=True)

        assert [node["code"].name for node in codebook_tree(project)] == ["Live"]
        assert [
            node["code"].name for node in codebook_tree(project, include_archived=True)
        ] == [
            "Live",
            "Retired",
        ]

    def test_marks_codes_missing_an_exclusion_rule(self):
        project = ProjectFactory()
        CodeFactory(project=project, name="No exclusion")
        CodeFactory(project=project, name="Has exclusion", exclusion="Not about zoning")

        tree = codebook_tree(project)

        assert tree[0]["missing_exclusion"] is True
        assert tree[1]["missing_exclusion"] is False

    def test_lists_exemplars_under_their_code(self):
        project = ProjectFactory()
        code = CodeFactory(project=project)
        page = MeetingPageFactory(text="Exemplar text")
        CodingFactory(project=project, code=code, page=page, is_exemplar=True)

        tree = codebook_tree(project)

        assert [coding.page_id for coding in tree[0]["exemplars"]] == [page.id]

    def test_skips_codings_with_missing_pages(self):
        project = ProjectFactory()
        code = CodeFactory(project=project)
        page = MeetingPageFactory(text="Surviving page")
        CodingFactory(project=project, code=code, page=page, is_exemplar=True)
        Coding.objects.create(
            project=project,
            code=code,
            page_id="missing-page",
            is_exemplar=True,
        )

        tree = codebook_tree(project)

        assert tree[0]["coding_count"] == 1
        assert [coding.page_id for coding in tree[0]["exemplars"]] == [page.id]


@pytest.mark.django_db
class TestCreateAndRenameCode:
    def test_creates_a_code_and_records_an_event(self):
        project = ProjectFactory()
        actor = UserFactory()

        code = create_code(
            project,
            name="Housing",
            color="#ff0000",
            definition="Housing policy",
            inclusion="When housing is discussed",
            exclusion="When housing is only mentioned in passing",
            actor=actor,
        )

        assert code.project == project
        assert code.name == "Housing"
        assert code.color == "#ff0000"
        event = project.events.get(verb="code.created")
        assert event.actor == actor
        assert event.payload["name"] == "Housing"

    def test_rejects_a_blank_name(self):
        project = ProjectFactory()

        with pytest.raises(ValueError):
            create_code(project, name="   ")

    def test_rejects_a_duplicate_sibling_name(self):
        project = ProjectFactory()
        create_code(project, name="Housing")

        with pytest.raises(ValueError):
            create_code(project, name="Housing")

    def test_rejects_a_child_of_a_child(self):
        project = ProjectFactory()
        parent = create_code(project, name="Housing")
        child = create_code(project, name="Affordable housing", parent=parent)

        with pytest.raises(ValueError):
            create_code(project, name="Too deep", parent=child)

    def test_rejects_a_parent_from_another_project(self):
        project = ProjectFactory()
        other = ProjectFactory()
        foreign = create_code(other, name="Foreign")

        with pytest.raises(ValueError):
            create_code(project, name="Child", parent=foreign)

    def test_rename_records_before_and_after(self):
        project = ProjectFactory()
        code = create_code(project, name="Housing")

        rename_code(code, name="Housing policy", color="#00ff00")

        code.refresh_from_db()
        assert code.name == "Housing policy"
        assert code.color == "#00ff00"
        event = project.events.get(verb="code.changed")
        assert event.payload["before"]["name"] == "Housing"
        assert event.payload["after"]["name"] == "Housing policy"


@pytest.mark.django_db
class TestArchiveCode:
    def test_archives_the_code_and_its_children(self):
        project = ProjectFactory()
        parent = create_code(project, name="Housing")
        child = create_code(project, name="Affordable housing", parent=parent)

        archive_code(parent)

        parent.refresh_from_db()
        child.refresh_from_db()
        assert parent.is_archived is True
        assert child.is_archived is True
        event = project.events.get(verb="code.archived")
        assert event.payload["children"] == [str(child.id)]


@pytest.mark.django_db
class TestMergeCodes:
    def test_moves_codings_and_deletes_the_source(self):
        project = ProjectFactory()
        source = create_code(project, name="Housing")
        target = create_code(project, name="Housing policy")
        page = MeetingPageFactory()
        CodingFactory(project=project, code=source, page=page)
        CodingFactory(project=project, code=target, page=MeetingPageFactory())

        moved = merge_codes(source, target)

        assert moved == 1
        assert Code.objects.filter(pk=source.pk).exists() is False
        assert Coding.objects.filter(code=target).count() == 2
        event = project.events.get(verb="code.merged")
        assert event.payload["source"] == str(source.id)
        assert event.payload["target"] == str(target.id)
        assert event.payload["moved"] == 1

    def test_does_not_duplicate_when_both_codes_have_the_page(self):
        project = ProjectFactory()
        source = create_code(project, name="Housing")
        target = create_code(project, name="Housing policy")
        page = MeetingPageFactory()
        CodingFactory(project=project, code=source, page=page)
        CodingFactory(project=project, code=target, page=page)

        moved = merge_codes(source, target)

        assert moved == 0
        assert Coding.objects.filter(code=target).count() == 1

    def test_rejects_merging_a_code_with_children(self):
        project = ProjectFactory()
        source = create_code(project, name="Housing")
        create_code(project, name="Affordable housing", parent=source)
        target = create_code(project, name="Housing policy")

        with pytest.raises(ValueError):
            merge_codes(source, target)

    def test_rejects_cross_project_merge(self):
        project = ProjectFactory()
        source = create_code(project, name="Housing")
        target = create_code(ProjectFactory(), name="Housing policy")

        with pytest.raises(ValueError):
            merge_codes(source, target)


@pytest.mark.django_db
class TestApplyAndRemoveCode:
    def test_apply_records_quote_hash_and_event(self):
        project, page, result_set = _project_with_page()
        code = create_code(project, name="Housing")
        actor = UserFactory()

        codings = apply_code_to_pages(
            project, code, [page.id], actor=actor, result_set=result_set
        )

        assert len(codings) == 1
        coding = Coding.objects.get(code=code, page=page)
        assert coding.quote == page.text
        assert coding.page_hash == page.content_hash
        assert coding.coder == actor
        event = result_set.project.events.get(verb="items.coded")
        assert event.payload["code"] == str(code.id)
        assert event.payload["page_ids"] == [page.id]
        assert event.payload["count"] == 1

    def test_duplicate_apply_writes_no_event(self):
        project, page, result_set = _project_with_page()
        code = create_code(project, name="Housing")
        apply_code_to_pages(project, code, [page.id], result_set=result_set)

        codings = apply_code_to_pages(project, code, [page.id], result_set=result_set)

        assert codings == []
        assert result_set.project.events.filter(verb="items.coded").count() == 1

    def test_skips_missing_pages(self):
        project, _page, result_set = _project_with_page()
        code = create_code(project, name="Housing")

        codings = apply_code_to_pages(
            project, code, ["missing-page"], result_set=result_set
        )

        assert codings == []

    def test_apply_to_lens_covers_only_the_lens(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        in_lens = MeetingPageFactory(state="CA")
        outside = MeetingPageFactory(state="OR")
        SetItemFactory(result_set=result_set, page=in_lens, state="CA")
        SetItemFactory(result_set=result_set, page=outside, state="OR")
        code = create_code(project, name="Housing")

        codings = apply_code_to_lens(project, code, result_set, Lens(states=("CA",)))

        assert [coding.page_id for coding in codings] == [in_lens.id]

    def test_remove_records_uncoded_event(self):
        project, page, result_set = _project_with_page()
        code = create_code(project, name="Housing")
        apply_code_to_pages(project, code, [page.id], result_set=result_set)

        removed = remove_code_from_pages(
            project, code, [page.id], result_set=result_set
        )

        assert removed == 1
        assert Coding.objects.filter(code=code, page=page).exists() is False
        event = result_set.project.events.get(verb="items.uncoded")
        assert event.payload["code"] == str(code.id)
        assert event.payload["count"] == 1

    def test_remove_from_lens_covers_only_the_lens(self):
        project = ProjectFactory()
        result_set = ResultSetFactory(project=project)
        in_lens = MeetingPageFactory(state="CA")
        outside = MeetingPageFactory(state="OR")
        SetItemFactory(result_set=result_set, page=in_lens, state="CA")
        SetItemFactory(result_set=result_set, page=outside, state="OR")
        code = create_code(project, name="Housing")
        apply_code_to_pages(
            project, code, [in_lens.id, outside.id], result_set=result_set
        )

        removed = remove_code_from_lens(project, code, result_set, Lens(states=("CA",)))

        assert removed == 1
        assert Coding.objects.filter(code=code, page=in_lens).exists() is False
        assert Coding.objects.filter(code=code, page=outside).exists() is True


@pytest.mark.django_db
class TestExemplars:
    def test_marks_and_unmarks_an_exemplar(self):
        project, page, _result_set = _project_with_page()
        code = create_code(project, name="Housing")
        coding = Coding.objects.create(project=project, code=code, page=page)

        set_exemplar(coding, is_exemplar=True)
        coding.refresh_from_db()
        assert coding.is_exemplar is True

        set_exemplar(coding, is_exemplar=False)
        coding.refresh_from_db()
        assert coding.is_exemplar is False

    def test_exemplar_event_records_before_and_after(self):
        project, page, _result_set = _project_with_page()
        code = create_code(project, name="Housing")
        coding = Coding.objects.create(project=project, code=code, page=page)

        set_exemplar(coding, is_exemplar=True)

        event = project.events.get(verb="code.changed")
        assert event.payload["before"]["is_exemplar"] is False
        assert event.payload["after"]["is_exemplar"] is True
