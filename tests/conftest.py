import pytest
from django.contrib.auth import get_user_model
from django.test import Client

User = get_user_model()


@pytest.fixture
def user_data():
    return {
        "username": "testuser",
        "email": "test@example.com",
        "password": "testpass123",
        "first_name": "Test",
        "last_name": "User",
    }


@pytest.fixture
def user(db, user_data):
    return User.objects.create_user(**user_data)


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="adminpass123",
    )


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def authenticated_client(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def admin_client(client, superuser):
    client.force_login(superuser)
    return client
