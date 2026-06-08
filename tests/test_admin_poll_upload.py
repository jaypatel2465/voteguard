import json
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import Flask

from config import Config
from models.database import Database
from modules.security import hash_aadhar
import routes.admin as admin_routes


def create_test_app(test_token, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(Config, 'DATABASE_PATH', str(root / f'test_voting_{test_token}.db'))
    monkeypatch.setattr(Config, 'FACE_DATASET_FOLDER', str(root / f'test_face_dataset_{test_token}'))
    db = Database()
    monkeypatch.setattr(admin_routes, 'db', db)

    app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
    app.secret_key = 'test-secret'
    app.register_blueprint(admin_routes.admin_bp)
    return app, db


def create_user(db, *, first_name, last_name, aadhar, phone, email):
    result = db.create_user(
        first_name,
        '',
        last_name,
        hash_aadhar(aadhar),
        aadhar[-4:],
        phone,
        email,
        'password123'
    )
    assert result['success'] is True
    return result['user_id']


def login_admin_session(client, admin_id=1):
    with client.session_transaction() as session:
        session['admin_logged_in'] = True
        session['admin_id'] = admin_id
        session['admin_email'] = 'admin@voting.com'


def create_test_token():
    return uuid4().hex


def test_upload_voters_assigns_matching_users(monkeypatch):
    app, db = create_test_app(create_test_token(), monkeypatch)
    poll = db.create_poll('Campus Election', admin_id=1)
    poll_id = poll['poll_id']

    user_one = create_user(
        db,
        first_name='Asha',
        last_name='Patel',
        aadhar='123456789012',
        phone='9876543210',
        email='asha@example.com'
    )
    user_two = create_user(
        db,
        first_name='Rahul',
        last_name='Singh',
        aadhar='987654321098',
        phone='9123456780',
        email='rahul@example.com'
    )

    client = app.test_client()
    login_admin_session(client)

    csv_data = '\n'.join([
        'aadhar_number,email',
        '123456789012,',
        ',rahul@example.com'
    ])
    response = client.post(
        f'/admin/poll/{poll_id}/upload-voters',
        data={'file': (BytesIO(csv_data.encode('utf-8')), 'voters.csv')},
        content_type='multipart/form-data'
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['total'] == 2
    assert payload['assigned'] == 2
    assert payload['not_found'] == 0
    assert payload['duplicates'] == 0

    eligible_ids = {user['id'] for user in db.get_eligible_users_for_poll(poll_id)}
    assert eligible_ids == {user_one, user_two}


def test_upload_voters_reports_invalid_duplicates_and_existing_assignments(monkeypatch):
    app, db = create_test_app(create_test_token(), monkeypatch)
    poll = db.create_poll('City Council Poll', admin_id=1)
    poll_id = poll['poll_id']

    assigned_user = create_user(
        db,
        first_name='Meera',
        last_name='Shah',
        aadhar='123456789012',
        phone='9000000001',
        email='meera@example.com'
    )
    new_user = create_user(
        db,
        first_name='Vikram',
        last_name='Rao',
        aadhar='987654321098',
        phone='9000000002',
        email='vikram@example.com'
    )
    db.add_users_to_poll(poll_id, [assigned_user])

    client = app.test_client()
    login_admin_session(client)

    csv_data = '\n'.join([
        'aadhar_number,email,phone',
        '123456789012,,',
        ',meera@example.com,',
        '999999999999,,',
        ',vikram@example.com,'
    ])
    response = client.post(
        f'/admin/poll/{poll_id}/upload-voters',
        data={'file': (BytesIO(csv_data.encode('utf-8')), 'assignments.csv')},
        content_type='multipart/form-data'
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['total'] == 4
    assert payload['assigned'] == 1
    assert payload['not_found'] == 1
    assert payload['duplicates'] == 2

    eligible_ids = {user['id'] for user in db.get_eligible_users_for_poll(poll_id)}
    assert eligible_ids == {assigned_user, new_user}

    payload_text = json.dumps(payload)
    assert '123456789012' not in payload_text
    assert '999999999999' not in payload_text


def test_upload_voters_rejects_non_csv_files(monkeypatch):
    app, db = create_test_app(create_test_token(), monkeypatch)
    poll = db.create_poll('School Poll', admin_id=1)
    poll_id = poll['poll_id']

    client = app.test_client()
    login_admin_session(client)

    response = client.post(
        f'/admin/poll/{poll_id}/upload-voters',
        data={'file': (BytesIO(b'not-a-csv'), 'voters.txt')},
        content_type='multipart/form-data'
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['success'] is False
    assert 'Only CSV files are allowed' in payload['error']


def test_get_poll_access_includes_manual_search_fields(monkeypatch):
    app, db = create_test_app(create_test_token(), monkeypatch)
    poll = db.create_poll('Ward Election', admin_id=1)
    poll_id = poll['poll_id']

    user_id = create_user(
        db,
        first_name='Neha',
        last_name='Kapoor',
        aadhar='123456789012',
        phone='9988776655',
        email='neha@example.com'
    )
    db.add_users_to_poll(poll_id, [user_id])

    client = app.test_client()
    login_admin_session(client)

    response = client.get(f'/admin/polls/{poll_id}/access')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['poll_title'] == 'Ward Election'
    assert payload['users'] == [{
        'id': user_id,
        'name': 'Neha Kapoor',
        'email': 'neha@example.com',
        'phone': '9988776655',
        'aadhar_last4': '9012',
        'assigned': True
    }]


def test_manage_polls_page_shows_manual_selection_and_csv_limit(monkeypatch):
    monkeypatch.setattr(Config, 'MAX_VOTER_CSV_SIZE_BYTES', 5 * 1024 * 1024)
    app, db = create_test_app(create_test_token(), monkeypatch)
    db.create_poll('Campus Election', admin_id=1)

    client = app.test_client()
    login_admin_session(client)

    response = client.get('/admin/polls')

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Manual Select Voters' in html
    assert 'Maximum file size: 5 MB' in html


def test_upload_voters_rejects_csv_larger_than_config_limit(monkeypatch):
    monkeypatch.setattr(Config, 'MAX_VOTER_CSV_SIZE_BYTES', 1 * 1024 * 1024)
    app, db = create_test_app(create_test_token(), monkeypatch)
    poll = db.create_poll('College Poll', admin_id=1)
    poll_id = poll['poll_id']

    client = app.test_client()
    login_admin_session(client)

    csv_data = 'email\n' + ('oversized@example.com\n' * 70000)
    response = client.post(
        f'/admin/poll/{poll_id}/upload-voters',
        data={'file': (BytesIO(csv_data.encode('utf-8')), 'oversized.csv')},
        content_type='multipart/form-data'
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['success'] is False
    assert 'Maximum size is 1 MB' in payload['error']
