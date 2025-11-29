from datetime import date
from typing import Any

import factory
from django.contrib.auth import get_user_model
from factory.django import DjangoModelFactory

from meetings.models import MeetingDocument, MeetingPage
from municipalities.models import Muni
from notebooks.models import Notebook, Tag
from searches.models import SavedSearch, Search

User = get_user_model()


class UserFactory(DjangoModelFactory):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user{n}")  # type: ignore
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@example.com")  # type: ignore
    first_name = factory.Faker("first_name")  # type: ignore
    last_name = factory.Faker("last_name")  # type: ignore
    is_active = True
    is_staff = False
    is_superuser = False

    @factory.post_generation  # type: ignore
    def password(obj: Any, create: bool, extracted: Any, **kwargs: Any) -> None:
        if not create:
            return
        password = extracted or "defaultpass123"
        obj.set_password(password)
        obj.save()


class AdminUserFactory(UserFactory):
    is_staff = True
    is_superuser = True
    username = factory.Sequence(lambda n: f"admin{n}")  # type: ignore
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@example.com")  # type: ignore


class MuniFactory(DjangoModelFactory):
    class Meta:
        model = Muni

    subdomain = factory.Sequence(lambda n: f"city{n}")  # type: ignore
    name = factory.Faker("city")  # type: ignore
    state = "CA"
    country = "US"
    kind = "city"
    pages = 0


class MeetingDocumentFactory(DjangoModelFactory):
    class Meta:
        model = MeetingDocument

    municipality = factory.SubFactory(MuniFactory)  # type: ignore
    meeting_name = "CityCouncil"
    meeting_date = factory.LazyFunction(lambda: date(2024, 1, 15))  # type: ignore
    document_type = "agenda"


class MeetingPageFactory(DjangoModelFactory):
    class Meta:
        model = MeetingPage

    id = factory.Sequence(lambda n: f"page-{n}")  # type: ignore
    document = factory.SubFactory(MeetingDocumentFactory)  # type: ignore
    page_number = factory.Sequence(lambda n: n + 1)  # type: ignore  # Unique page numbers
    text = factory.Faker("text")  # type: ignore
    page_image = factory.LazyAttribute(  # type: ignore
        lambda obj: f"/_agendas/{obj.document.meeting_name}/{obj.document.meeting_date}/{obj.page_number}.png"
    )


class SearchFactory(DjangoModelFactory):
    class Meta:
        model = Search

    search_term = factory.Faker("word")  # type: ignore
    meeting_name_query = ""  # CharField with blank=True, default=""
    states: list[str] = []
    document_type = "all"

    @factory.post_generation  # type: ignore
    def municipalities(obj: Any, create: bool, extracted: Any, **kwargs: Any) -> None:
        """Handle M2M relationship for municipalities."""
        if not create:
            return

        if extracted:
            # A list of municipalities was passed in
            for muni in extracted:
                obj.municipalities.add(muni)
        # Otherwise leave municipalities empty (can be added in test)


class SavedSearchFactory(DjangoModelFactory):
    class Meta:
        model = SavedSearch

    user = factory.SubFactory(UserFactory)  # type: ignore
    search = factory.SubFactory(SearchFactory)  # type: ignore
    name = factory.Faker("sentence", nb_words=3)  # type: ignore


class NotebookFactory(DjangoModelFactory):
    class Meta:
        model = Notebook

    user = factory.SubFactory(UserFactory)  # type: ignore
    name = factory.Faker("sentence", nb_words=3)  # type: ignore
    is_archived = False


class TagFactory(DjangoModelFactory):
    class Meta:
        model = Tag

    user = factory.SubFactory(UserFactory)  # type: ignore
    name = factory.Faker("word")  # type: ignore
