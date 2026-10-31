import os
import pytest
import tempfile
import sqlite3
from app import app
import database
from models import (
    calculate_age, calculate_bmi, calculate_distance, calculate_match_score
)


@pytest.fixture
def client(monkeypatch):
    """Set up temporary database and test client."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = os.path.join(tmpdir, "test_lifelink.db")
        monkeypatch.setattr(database, "DB_PATH", test_db)
        database.init_db()

        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        with app.test_client() as client:
            yield client


def test_calculate_bmi():
    # 70kg, 175cm -> 70 / (1.75^2) = 22.86
    bmi = calculate_bmi(70, 175)
    assert 22.8 <= bmi <= 22.9
    assert calculate_bmi(0, 175) == 0.0
    assert calculate_bmi(70, 0) == 0.0


def test_calculate_age():
    age = calculate_age("2000-01-01")
    assert age >= 24
    assert calculate_age(None) == 0


def test_calculate_distance():
    # Mumbai to Pune ~150km
    dist = calculate_distance("Mumbai", "Pune")
    assert dist > 0
    # Same city
    assert calculate_distance("Mumbai", "Mumbai") == 0


def test_calculate_match_score_compatible():
    donor = {
        'donor_id': 'DN-001',
        'blood_group': 'O+',
        'organ_type': 'Kidney',
        'weight_kg': 70,
        'height_cm': 175,
        'location': 'Mumbai',
        'dob': '1995-01-01',
        'medical_history': 'None'
    }
    patient = {
        'patient_id': 'PT-001',
        'blood_group': 'O+',
        'organ_needed': 'Kidney',
        'urgency_score': 85,
        'weight_kg': 68,
        'height_cm': 172,
        'location': 'Mumbai',
        'dob': '1993-05-15',
        'medical_history': 'None'
    }
    score, reasons = calculate_match_score(patient, donor)
    assert score >= 50
    assert len(reasons) > 0


def test_calculate_match_score_incompatible_organ():
    donor = {'organ_type': 'Liver', 'blood_group': 'O+'}
    patient = {'organ_needed': 'Kidney', 'blood_group': 'O+'}
    score, reasons = calculate_match_score(patient, donor)
    assert score == 0


def test_public_routes(client):
    resp = client.get("/")
    assert resp.status_code == 200

    resp = client.get("/login")
    assert resp.status_code == 200

    resp = client.get("/signup")
    assert resp.status_code == 200


def test_auth_protection_redirect(client):
    # Protected route should redirect to login when unauthenticated
    resp = client.get("/dashboard")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
