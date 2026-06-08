import time

import modules.liveness as liveness


class SessionDict(dict):
    modified = False


def test_validate_liveness_submission_allows_short_submit_grace(monkeypatch):
    now = time.time()
    session = SessionDict({
        'liveness_login': {
            'id': 'challenge-1',
            'action': 'blink',
            'expires_at': now - 1
        }
    })
    payload = {
        'challenge_id': 'challenge-1',
        'action': 'blink',
        'passed': True,
        'completed_at': now - 0.5,
        'metrics': {
            'blink_count': 1,
            'min_ear': 0.16,
            'max_ear': 0.31
        }
    }

    monkeypatch.setattr(liveness, '_validate_browser_metrics', lambda action, payload: {'success': True})
    monkeypatch.setattr(liveness, '_validate_frame_sequence', lambda frames, action: {'success': True})

    result = liveness.validate_liveness_submission(session, 'login', payload, [])

    assert result['success'] is True
    assert 'liveness_login' not in session


def test_validate_liveness_submission_rejects_after_grace_window(monkeypatch):
    now = time.time()
    session = SessionDict({
        'liveness_login': {
            'id': 'challenge-2',
            'action': 'blink',
            'expires_at': now - 20
        }
    })
    payload = {
        'challenge_id': 'challenge-2',
        'action': 'blink',
        'passed': True,
        'completed_at': now - 19,
        'metrics': {
            'blink_count': 1,
            'min_ear': 0.16,
            'max_ear': 0.31
        }
    }

    monkeypatch.setattr(liveness, '_validate_browser_metrics', lambda action, payload: {'success': True})
    monkeypatch.setattr(liveness, '_validate_frame_sequence', lambda frames, action: {'success': True})

    result = liveness.validate_liveness_submission(session, 'login', payload, [])

    assert result['success'] is False
    assert result['error_code'] == 'liveness_expired'
