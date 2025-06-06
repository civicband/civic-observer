from typing import Any

import factory
from django.contrib.auth import get_user_model
from factory.django import DjangoModelFactory

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
