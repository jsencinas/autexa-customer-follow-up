import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from main import app
from app.config import settings


@pytest.fixture
def client():
    return TestClient(app)


class TestWebhookVerification:
    """Tests for the Meta verification handshake."""

    def test_valid_verification(self, client):
        """A valid verify token returns the challenge."""
        resp = client.get("/webhook", params={
            "hub.mode": "subscribe",
            "hub.verify_token": settings.whatsapp_verify_token,
            "hub.challenge": "test_challenge_123"
        })
        assert resp.status_code == 200
        assert resp.text == "test_challenge_123"

    def test_invalid_verification(self, client):
        """An invalid verify token returns 403."""
        resp = client.get("/webhook", params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "test_challenge_123"
        })
        assert resp.status_code == 403


class TestSurveysEndpoint:
    """Tests for the protected surveys listing endpoint."""

    def test_missing_api_key_returns_401(self, client):
        """Requests without API key are rejected."""
        resp = client.get("/surveys")
        assert resp.status_code == 401

    def test_wrong_api_key_returns_401(self, client):
        """Requests with a wrong API key are rejected."""
        resp = client.get("/surveys", headers={"X-API-Key": "wrong_key"})
        assert resp.status_code == 401

    def test_valid_api_key_returns_results(self, client):
        """A valid API key returns the survey listing."""
        resp = client.get("/surveys", headers={"X-API-Key": settings.api_key})
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "results" in data

    def test_csv_export(self, client):
        """CSV export returns a downloadable file."""
        resp = client.get("/surveys/export", headers={"X-API-Key": settings.api_key})
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
