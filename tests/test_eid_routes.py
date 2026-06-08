from pathlib import Path
from uuid import uuid4

from flask import Flask

import routes.user as user_routes


def create_app():
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
    app.secret_key = 'test-secret'
    app.register_blueprint(user_routes.user_bp)

    @app.route('/verify/<eid_hash>')
    def verify_voter_public(eid_hash):
        return f'Verified {eid_hash}'

    return app


def login_user_session(client, user_id=7, email='voter@example.com'):
    with client.session_transaction() as session:
        session['user_logged_in'] = True
        session['user_id'] = user_id
        session['user_email'] = email


def sample_user(**overrides):
    base_user = {
        'id': 7,
        'first_name': 'Asha',
        'last_name': 'Patel',
        'email': 'asha@example.com',
        'phone': '9876543210',
        'aadhar_last4': '9012',
        'aadhar_hash': 'hash-value',
        'profile_image': 'user_7_photo.jpg',
        'eid_hash': 'ABCDEF1234567890ABCD',
        'eid_pdf_path': str(Path(__file__).resolve())
    }
    base_user.update(overrides)
    return base_user


def test_id_card_page_renders_existing_eid_assets(monkeypatch):
    app = create_app()
    user = sample_user()
    monkeypatch.setattr(user_routes, '_refresh_eid_assets', lambda user_id, receipt_url_base, force_regenerate=False: user)

    client = app.test_client()
    login_user_session(client, user_id=user['id'], email=user['email'])

    response = client.get('/user/id-card/view')

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Your Voter E-ID Card' in body
    assert user['eid_hash'] in body
    assert f"/verify/{user['eid_hash']}" in body


def test_id_card_qr_returns_svg_for_verification_url(monkeypatch):
    app = create_app()
    user = sample_user()
    captured = {}

    monkeypatch.setattr(user_routes, '_refresh_eid_assets', lambda user_id, receipt_url_base, force_regenerate=False: user)

    def fake_build_qr_svg(value, size=168):
        captured['value'] = value
        return f'<svg>{value}</svg>'

    monkeypatch.setattr(user_routes, '_build_qr_svg', fake_build_qr_svg)

    client = app.test_client()
    login_user_session(client, user_id=user['id'], email=user['email'])

    response = client.get('/user/id-card/qr')

    assert response.status_code == 200
    assert response.mimetype == 'image/svg+xml'
    assert user['eid_hash'] in captured['value']
    assert user['eid_hash'] in response.get_data(as_text=True)


def test_id_card_download_serves_generated_pdf(monkeypatch):
    app = create_app()
    root = Path(__file__).resolve().parents[1]
    pdf_path = root / f'test_runtime_tmp_eid_{uuid4().hex}.pdf'
    pdf_path.write_bytes(b'%PDF-1.4 test pdf')
    user = sample_user(eid_pdf_path=str(pdf_path))

    monkeypatch.setattr(user_routes, '_refresh_eid_assets', lambda user_id, receipt_url_base, force_regenerate=False: user)

    client = app.test_client()
    login_user_session(client, user_id=user['id'], email=user['email'])

    response = None
    payload = b''
    try:
        response = client.get('/user/id-card')
        payload = response.get_data()
    finally:
        if response is not None:
            response.close()
        if pdf_path.exists():
            pdf_path.unlink()

    assert response.status_code == 200
    assert payload.startswith(b'%PDF-1.4')
