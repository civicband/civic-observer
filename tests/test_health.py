"""Tests for the health check endpoint."""

from unittest.mock import patch

import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_health_check_ok(client):
    response = client.get(reverse("health_check"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_health_check_unhealthy_when_db_unreachable(client):
    with patch("config.views.connections") as mock_connections:
        mock_connections.all.side_effect = RuntimeError("db down")
        response = client.get(reverse("health_check"))

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy"}
