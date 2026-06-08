from io import BytesIO

from flask import Flask

import routes.user as user_routes
import routes.voting as voting_routes


class DummyUserDB:
    def log_face_attempt(self, **kwargs):
        return {'success': True}

    def get_user_by_id(self, user_id):
        return {'id': user_id, 'email': 'user@example.com', 'aadhar_last4': '1234'}


class DummyVotingDB:
    def log_face_attempt(self, **kwargs):
        return {'success': True}

    def get_poll_by_id(self, poll_id):
        return {'id': poll_id, 'allow_nota': 0}

    def user_has_poll_access(self, user_id, poll_id):
        return True

    def is_poll_active(self, poll_id):
        return True

    def has_user_voted(self, user_id, poll_id=None):
        return False


class AmbiguousRecognizer:
    def recognize_face_from_images(self, face_images, detection_confidences=None):
        return {
            'success': False,
            'error': 'Face match is ambiguous. Please use email and password.',
            'error_code': 'ambiguous_match',
            'confidence': 0.82,
            'winner_user_id': 3,
            'runner_up_user_id': 4,
            'winner_score': 0.82,
            'runner_up_score': 0.79,
            'score_margin': 0.03,
            'valid_frame_count': 10,
            'detection_confidence': 0.99,
        }


class VerifyRecognizer:
    def verify_user_from_images(self, user_id, face_images, detection_confidences=None):
        return {
            'success': True,
            'user_id': user_id,
            'confidence': 0.92,
            'winner_user_id': user_id,
            'runner_up_user_id': 9,
            'winner_score': 0.92,
            'runner_up_score': 0.70,
            'score_margin': 0.22,
            'valid_frame_count': 10,
            'detection_confidence': 0.99,
        }


def create_app():
    app = Flask(__name__)
    app.secret_key = 'test-secret'
    app.register_blueprint(user_routes.user_bp)
    app.register_blueprint(voting_routes.voting_bp)
    return app


def test_login_face_web_returns_fallback_for_ambiguous_match(monkeypatch):
    app = create_app()
    monkeypatch.setattr(user_routes, 'db', DummyUserDB())
    monkeypatch.setattr(user_routes, 'validate_liveness_submission', lambda session, context, raw_payload, frames: {'success': True})
    monkeypatch.setattr(user_routes, '_collect_face_images', lambda frames: ([object()] * 10, [0.99] * 10))
    monkeypatch.setattr(user_routes, 'FaceRecognizer', lambda: AmbiguousRecognizer())

    client = app.test_client()
    response = client.post(
        '/user/login-face-web',
        data={
            'liveness_data': '{"passed": true}',
            'frames': (BytesIO(b'frame-data'), 'frame.jpg')
        },
        content_type='multipart/form-data'
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['error_code'] == 'ambiguous_match'
    assert payload['fallback_to_password'] is True


def test_verify_face_web_checks_logged_in_user(monkeypatch):
    app = create_app()
    monkeypatch.setattr(voting_routes, 'db', DummyVotingDB())
    monkeypatch.setattr(voting_routes, 'validate_liveness_submission', lambda session, context, raw_payload, frames: {'success': True})
    monkeypatch.setattr(voting_routes, '_collect_face_images', lambda frames: ([object()] * 10, [0.99] * 10))
    monkeypatch.setattr(voting_routes, 'FaceRecognizer', lambda: VerifyRecognizer())

    client = app.test_client()
    with client.session_transaction() as session:
        session['user_logged_in'] = True
        session['user_id'] = 7

    response = client.post(
        '/voting/verify-face-web',
        data={
            'poll_id': '2',
            'liveness_data': '{"passed": true}',
            'frames': (BytesIO(b'frame-data'), 'frame.jpg')
        },
        content_type='multipart/form-data'
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['user_id'] == 7


def test_login_liveness_challenge_issues_challenge(monkeypatch):
    app = create_app()
    monkeypatch.setattr(user_routes, 'issue_liveness_challenge', lambda session, context, browser_mode=True: {
        'id': 'login-challenge',
        'action': 'blink',
        'prompt': 'Please blink your eyes',
        'expires_at': 9999999999
    })

    client = app.test_client()
    response = client.post('/user/liveness-challenge')

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['enabled'] is True
    assert payload['challenge_id'] == 'login-challenge'
    assert payload['action'] == 'blink'


def test_verify_face_web_rejects_when_liveness_fails(monkeypatch):
    app = create_app()
    monkeypatch.setattr(voting_routes, 'db', DummyVotingDB())
    monkeypatch.setattr(voting_routes, 'validate_liveness_submission', lambda session, context, raw_payload, frames: {
        'success': False,
        'error': 'Complete the liveness challenge before continuing.',
        'error_code': 'liveness_required'
    })

    client = app.test_client()
    with client.session_transaction() as session:
        session['user_logged_in'] = True
        session['user_id'] = 7

    response = client.post(
        '/voting/verify-face-web',
        data={
            'poll_id': '2',
            'frames': (BytesIO(b'frame-data'), 'frame.jpg')
        },
        content_type='multipart/form-data'
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload['error_code'] == 'liveness_required'


def test_voting_liveness_challenge_requires_logged_in_user(monkeypatch):
    app = create_app()
    monkeypatch.setattr(voting_routes, 'db', DummyVotingDB())
    monkeypatch.setattr(voting_routes, 'issue_liveness_challenge', lambda session, context, browser_mode=True: {
        'id': 'vote-challenge',
        'action': 'turn_left',
        'prompt': 'Turn your head left',
        'expires_at': 9999999999
    })

    client = app.test_client()
    with client.session_transaction() as session:
        session['user_logged_in'] = True
        session['user_id'] = 7

    response = client.post('/voting/liveness-challenge', json={'poll_id': 2})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['challenge_id'] == 'vote-challenge'
    assert payload['action'] == 'turn_left'
