"""
Active liveness validation helpers.

This module adds a lightweight liveness gate before face recognition
"""
import json
import random
import time
from uuid import uuid4

import cv2
import numpy as np

from config import Config
from modules.face_detector import FaceDetector


CHALLENGE_COPY = {
    'blink': 'Please blink your eyes',
    'turn_left': 'Turn your head left',
    'turn_right': 'Turn your head right',
}


def _session_key(context):
    return f'liveness_{context}'


def issue_liveness_challenge(session, context='login', browser_mode=True):
    """Create and store a short-lived liveness challenge in the session."""
    if browser_mode:
        actions = ('blink', 'turn_left', 'turn_right')
    else:
        actions = ('turn_left', 'turn_right')

    action = random.choice(actions)
    now = time.time()
    challenge = {
        'id': uuid4().hex,
        'context': context,
        'action': action,
        'prompt': CHALLENGE_COPY[action],
        'issued_at': now,
        'expires_at': now + Config.LIVENESS_CHALLENGE_WINDOW_SECONDS,
        'browser_mode': browser_mode,
    }
    session[_session_key(context)] = challenge
    session.modified = True
    return challenge


def _build_result(success, message=None, error_code=None, **extra):
    payload = {'success': success}
    if success:
        payload['message'] = message or 'Liveness verified successfully.'
    else:
        payload['error'] = message or 'Liveness verification failed.'
        payload['error_code'] = error_code or 'liveness_failed'
    payload.update(extra)
    return payload


def _parse_liveness_payload(raw_payload):
    if not raw_payload:
        return None
    if isinstance(raw_payload, dict):
        return raw_payload
    try:
        return json.loads(raw_payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _restore_stream(file_storage, position):
    stream = getattr(file_storage, 'stream', None)
    if stream and position is not None:
        stream.seek(position)
    elif hasattr(file_storage, 'seek'):
        file_storage.seek(0)


def _decode_frame(file_storage):
    stream = getattr(file_storage, 'stream', None)
    position = stream.tell() if stream and hasattr(stream, 'tell') else None
    data = file_storage.read()
    _restore_stream(file_storage, position)
    if not data:
        return None
    buffer = np.frombuffer(data, np.uint8)
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)


def _validate_browser_metrics(action, payload):
    metrics = payload.get('metrics') or {}

    if action == 'blink':
        blink_count = int(metrics.get('blink_count', 0))
        min_ear = float(metrics.get('min_ear', 1.0))
        max_ear = float(metrics.get('max_ear', 0.0))
        if blink_count < 1:
            return _build_result(False, 'Please complete a full blink before continuing.', 'liveness_blink_missing')
        if max_ear <= 0 or min_ear >= max_ear:
            return _build_result(False, 'Blink quality was too weak. Please try again.', 'liveness_blink_quality')
        return _build_result(True)

    direction = metrics.get('direction')
    turn_delta = abs(float(metrics.get('turn_delta', 0.0)))
    expected = 'left' if action == 'turn_left' else 'right'
    if direction != expected:
        return _build_result(False, f'Please turn your head {expected} to continue.', 'liveness_direction_mismatch')
    if turn_delta < Config.LIVENESS_HEAD_TURN_THRESHOLD:
        return _build_result(False, f'Head movement was too small. Please turn {expected} a bit more.', 'liveness_turn_small')
    return _build_result(True)


def _validate_frame_sequence(frames, action):
    detector = FaceDetector()
    centers = []
    face_diffs = []
    previous_face = None

    for frame in frames:
        image = _decode_frame(frame)
        if image is None:
            continue

        bbox, _ = detector.detect_best_face(image)
        if bbox is None:
            continue

        x, y, w, h = bbox
        if w < Config.FACE_MIN_SIZE or h < Config.FACE_MIN_SIZE:
            continue

        face_crop = image[y:y + h, x:x + w]
        if face_crop is None or face_crop.size == 0:
            continue

        gray_face = cv2.cvtColor(cv2.resize(face_crop, (96, 96)), cv2.COLOR_BGR2GRAY)
        centers.append((x + (w / 2.0)) / float(image.shape[1]))

        if previous_face is not None:
            diff = cv2.absdiff(previous_face, gray_face)
            face_diffs.append(float(np.mean(diff)))
        previous_face = gray_face

    if len(centers) < Config.LIVENESS_MIN_FACE_FRAMES:
        return _build_result(False, 'Keep your face aligned in the frame and try again.', 'liveness_face_missing')

    average_variation = sum(face_diffs) / len(face_diffs) if face_diffs else 0.0
    movement_range = max(centers) - min(centers) if centers else 0.0

    if average_variation < Config.LIVENESS_MIN_FRAME_VARIATION:
        return _build_result(False, 'The capture looked too static. Please perform the liveness challenge again.', 'liveness_static_frames')

    if action in {'turn_left', 'turn_right'} and movement_range < Config.LIVENESS_MIN_FACE_MOVEMENT:
        return _build_result(False, 'Head movement was too small. Please try the challenge again.', 'liveness_motion_small')

    return _build_result(
        True,
        average_variation=average_variation,
        movement_range=movement_range,
        valid_face_frames=len(centers)
    )


def validate_liveness_submission(session, context, raw_payload, frames):
    """Validate the frontend liveness result before face recognition proceeds."""
    if not Config.LIVENESS_ENABLED:
        return _build_result(True, 'Liveness disabled.')

    challenge = session.get(_session_key(context))
    if not challenge:
        return _build_result(False, 'Liveness challenge not found. Please start again.', 'liveness_missing')

    payload = _parse_liveness_payload(raw_payload)
    if not payload:
        return _build_result(False, 'Complete the liveness challenge before continuing.', 'liveness_required')

    expires_at = float(challenge.get('expires_at', 0))
    grace_deadline = expires_at + Config.LIVENESS_SUBMISSION_GRACE_SECONDS
    now = time.time()
    completed_at = float(payload.get('completed_at') or 0)

    if now > grace_deadline:
        session.pop(_session_key(context), None)
        session.modified = True
        return _build_result(False, 'Liveness challenge expired. Please try again.', 'liveness_expired')

    if payload.get('challenge_id') != challenge.get('id'):
        return _build_result(False, 'Liveness session mismatch. Please retry.', 'liveness_mismatch')

    if payload.get('action') != challenge.get('action'):
        return _build_result(False, 'Unexpected liveness challenge response. Please retry.', 'liveness_action_mismatch')

    if not payload.get('passed'):
        return _build_result(False, 'Liveness challenge did not pass. Please try again.', 'liveness_failed')

    if completed_at and completed_at > grace_deadline:
        session.pop(_session_key(context), None)
        session.modified = True
        return _build_result(False, 'Liveness challenge expired. Please try again.', 'liveness_expired')

    metric_result = _validate_browser_metrics(challenge['action'], payload)
    if not metric_result['success']:
        return metric_result

    frame_result = _validate_frame_sequence(frames, challenge['action'])
    if not frame_result['success']:
        return frame_result

    session.pop(_session_key(context), None)
    session.modified = True
    return _build_result(True, 'Liveness verified successfully.', details=frame_result)


def verify_webcam_liveness(context='login'):
    """
    Lightweight webcam liveness fallback for non-browser capture.

    This uses the existing face detector and asks the user to turn left or right
    before the recognizer starts.
    """
    detector = FaceDetector()
    action = random.choice(('turn_left', 'turn_right'))
    prompt = CHALLENGE_COPY[action]
    deadline = time.time() + Config.LIVENESS_WEBCAM_WINDOW_SECONDS

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return _build_result(False, 'Unable to access webcam for liveness verification.', 'camera_unavailable')

    baseline_samples = []
    baseline_center = None
    passed = False

    window_name = 'Liveness Check'

    while time.time() < deadline:
        ret, frame = cap.read()
        if not ret:
            break

        bbox, conf = detector.detect_best_face(frame)
        status_text = prompt
        if bbox is not None:
            x, y, w, h = bbox
            center_ratio = (x + (w / 2.0)) / float(frame.shape[1])
            baseline_samples.append(center_ratio)
            baseline_samples = baseline_samples[-10:]
            if len(baseline_samples) >= 6:
                if baseline_center is None:
                    baseline_center = sum(baseline_samples) / len(baseline_samples)
                delta = center_ratio - baseline_center
                if action == 'turn_left' and delta <= -Config.LIVENESS_MIN_FACE_MOVEMENT:
                    passed = True
                elif action == 'turn_right' and delta >= Config.LIVENESS_MIN_FACE_MOVEMENT:
                    passed = True

            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(
                frame,
                f'Face: {conf:.2f}',
                (x, y - 10 if y - 10 > 10 else y + 16),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2
            )
        else:
            status_text = 'Align your face in the frame'

        remaining = max(0.0, deadline - time.time())
        cv2.putText(frame, prompt, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(frame, status_text, (10, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        cv2.putText(frame, f'Time left: {remaining:.1f}s', (10, 86), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        cv2.putText(frame, 'Press Q or ESC to cancel', (10, 114), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
        cv2.imshow(window_name, frame)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord('q'), 27):
            break
        if passed:
            break

    cap.release()
    cv2.destroyAllWindows()

    if passed:
        return _build_result(True, 'Liveness verified successfully.')
    return _build_result(False, 'Liveness verification failed. Please try again.', 'liveness_failed')
