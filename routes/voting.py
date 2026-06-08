"""
Voting Routes
Handles face verification and vote submission
"""
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app
from functools import wraps
import os
import cv2
import numpy as np
from models.database import Database
from config import Config
from modules.face_recognition import FaceRecognizer
from modules.pdf_generator import VoterReceiptPDFGenerator
from flask import send_file
from modules.face_detector import FaceDetector
from modules.face_embeddings import FaceEmbeddingHandler
from modules.liveness import issue_liveness_challenge, validate_liveness_submission, verify_webcam_liveness

voting_bp = Blueprint('voting', __name__, url_prefix='/voting')
db = Database()

def _decode_image(file_storage):
    if not file_storage:
        return None
    data = file_storage.read()
    if not data:
        return None
    nparr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    return img


def _collect_face_images(files):
    detector = FaceDetector()
    face_images = []
    detection_confidences = []
    for frame in files:
        img = _decode_image(frame)
        if img is None:
            continue
        bbox, conf = detector.detect_best_face(img)
        if bbox is None:
            continue
        x, y, w, h = bbox
        face_img = img[y:y + h, x:x + w]
        if face_img is None or face_img.size == 0:
            continue
        face_images.append(face_img)
        detection_confidences.append(float(conf))
    return face_images, detection_confidences


def _log_face_result(result, context, user_id, poll_id=None):
    db.log_face_attempt(
        user_id=user_id,
        context=context,
        detection_confidence=result.get('detection_confidence'),
        match_similarity=result.get('confidence'),
        success=1 if result.get('success') else 0,
        reason=None if result.get('success') else result.get('error_code') or result.get('error'),
        winner_user_id=result.get('winner_user_id'),
        runner_up_user_id=result.get('runner_up_user_id'),
        winner_score=result.get('winner_score'),
        runner_up_score=result.get('runner_up_score'),
        score_margin=result.get('score_margin'),
        valid_frame_count=result.get('valid_frame_count'),
        poll_id=poll_id
    )


def user_required(f):
    """Decorator to require user login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_logged_in' not in session:
            return redirect(url_for('user.login'))
        return f(*args, **kwargs)
    return decorated_function

@voting_bp.route('/verify-face', methods=['POST'])
@user_required
def verify_face():
    """Verify the logged-in user's face before allowing voting."""
    data = request.get_json() if request.is_json else request.form
    poll_id = data.get('poll_id') if data else request.args.get('poll_id')
    poll_id_int = int(poll_id) if poll_id and str(poll_id).isdigit() else None
    if not poll_id_int:
        return jsonify({'success': False, 'error': 'Poll selection required'}), 400

    poll = db.get_poll_by_id(poll_id_int)
    if not poll:
        return jsonify({'success': False, 'error': 'Invalid poll'}), 400

    if not db.user_has_poll_access(session['user_id'], poll_id_int):
        return jsonify({'success': False, 'error': 'You are not assigned to this poll'}), 403
    if not db.is_poll_active(poll_id_int):
        return jsonify({'success': False, 'error': 'Polling is currently closed'}), 403
    if db.has_user_voted(session['user_id'], poll_id_int):
        return jsonify({'success': False, 'error': 'You have already voted'}), 400

    try:
        liveness_result = verify_webcam_liveness('vote')
        if not liveness_result['success']:
            return jsonify({
                'success': False,
                'error': liveness_result['error'],
                'error_code': liveness_result.get('error_code')
            }), 400
        recognizer = FaceRecognizer()
        result = recognizer.verify_user_from_webcam(session['user_id'])
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    _log_face_result(result, 'vote', session['user_id'], poll_id=poll_id_int)

    if result['success']:
        session['face_verified'] = True
        session['face_verified_poll_id'] = poll_id_int
        return jsonify({
            'success': True,
            'user_id': session['user_id'],
            'message': 'Face verified successfully',
            'redirect': url_for('voting.voting_page', poll_id=poll_id_int)
        })

    return jsonify({
        'success': False,
        'error': result.get('error', 'Unable to detect. Please contact help desk.'),
        'error_code': result.get('error_code')
    }), 400


@voting_bp.route('/liveness-challenge', methods=['POST'])
@user_required
def voting_liveness_challenge():
    """Issue a short-lived liveness challenge before vote verification."""
    if not Config.LIVENESS_ENABLED:
        return jsonify({
            'success': True,
            'enabled': False,
            'message': 'Liveness detection is disabled.'
        })

    poll_id = request.form.get('poll_id') or (request.get_json(silent=True) or {}).get('poll_id') or request.args.get('poll_id')
    poll_id_int = int(poll_id) if poll_id and str(poll_id).isdigit() else None
    if poll_id_int:
        poll = db.get_poll_by_id(poll_id_int)
        if not poll:
            return jsonify({'success': False, 'error': 'Invalid poll'}), 400
        if not db.user_has_poll_access(session['user_id'], poll_id_int):
            return jsonify({'success': False, 'error': 'You are not assigned to this poll'}), 403
        if not db.is_poll_active(poll_id_int):
            return jsonify({'success': False, 'error': 'Polling is currently closed'}), 403
        if db.has_user_voted(session['user_id'], poll_id_int):
            return jsonify({'success': False, 'error': 'You have already voted'}), 400

    challenge = issue_liveness_challenge(session, context='vote', browser_mode=True)
    return jsonify({
        'success': True,
        'enabled': True,
        'challenge_id': challenge['id'],
        'action': challenge['action'],
        'prompt': challenge['prompt'],
        'expires_at': challenge['expires_at']
    })


@voting_bp.route('/verify-face-web', methods=['POST'])
@user_required
def verify_face_web():
    """Browser-based face verification before voting."""
    poll_id = request.form.get('poll_id') or request.args.get('poll_id')
    poll_id_int = int(poll_id) if poll_id and str(poll_id).isdigit() else None
    if not poll_id_int:
        return jsonify({'success': False, 'error': 'Poll selection required'}), 400

    poll = db.get_poll_by_id(poll_id_int)
    if not poll:
        return jsonify({'success': False, 'error': 'Invalid poll'}), 400

    if not db.user_has_poll_access(session['user_id'], poll_id_int):
        return jsonify({'success': False, 'error': 'You are not assigned to this poll'}), 403
    if not db.is_poll_active(poll_id_int):
        return jsonify({'success': False, 'error': 'Polling is currently closed'}), 403
    if db.has_user_voted(session['user_id'], poll_id_int):
        return jsonify({'success': False, 'error': 'You have already voted'}), 400

    frames = request.files.getlist('frames')
    if not frames:
        file = request.files.get('frame')
        frames = [file] if file else []
    if not frames:
        return jsonify({'success': False, 'error': 'No image uploaded'}), 400

    liveness_result = validate_liveness_submission(
        session,
        'vote',
        request.form.get('liveness_data'),
        frames
    )
    if not liveness_result['success']:
        return jsonify({
            'success': False,
            'error': liveness_result['error'],
            'error_code': liveness_result.get('error_code')
        }), 400

    try:
        face_images, detection_confidences = _collect_face_images(frames)
        recognizer = FaceRecognizer()
    except Exception as e:
        current_app.logger.exception('Failed to initialize face modules')
        return jsonify({'success': False, 'error': str(e)}), 500

    result = recognizer.verify_user_from_images(
        session['user_id'],
        face_images,
        detection_confidences=detection_confidences
    )
    _log_face_result(result, 'vote', session['user_id'], poll_id=poll_id_int)

    if not result['success']:
        return jsonify({
            'success': False,
            'error': result['error'],
            'error_code': result.get('error_code')
        }), 400

    session['face_verified'] = True
    session['face_verified_poll_id'] = poll_id_int
    return jsonify({
        'success': True,
        'user_id': session['user_id'],
        'message': 'Face verified successfully',
        'redirect': url_for('voting.voting_page', poll_id=poll_id_int)
    })


@voting_bp.route('/page')
@user_required
def voting_page():
    """Display voting page with candidates"""
    poll_id = request.args.get('poll_id') or session.get('face_verified_poll_id')
    poll_id_int = int(poll_id) if poll_id and str(poll_id).isdigit() else None
    if not poll_id_int:
        return redirect(url_for('user.home'))
    poll = db.get_poll_by_id(poll_id_int)
    if not poll:
        return redirect(url_for('user.home'))

    # Access control: user must be assigned to this poll
    if not db.user_has_poll_access(session['user_id'], poll_id_int):
        return render_template('user/success.html',
                             message='You are not assigned to this poll.',
                             redirect_url=url_for('user.home'))

    # Check if poll is active
    if not db.is_poll_active(poll_id_int):
        return render_template('user/success.html',
                             message='Polling is currently closed.',
                             redirect_url=url_for('user.home'))

    # Check if face was verified
    if not session.get('face_verified') or session.get('face_verified_poll_id') != poll_id_int:
        return redirect(url_for('user.home'))
    
    # Check if user already voted
    if db.has_user_voted(session['user_id'], poll_id_int):
        return render_template('user/success.html', 
                             message='You have already voted',
                             redirect_url=url_for('user.home'))
    
    # Get all candidates
    include_nota = poll.get('allow_nota', 0) == 1
    candidates = db.get_candidates_by_poll(poll_id_int, include_nota=include_nota)
    
    return render_template('user/voting.html', candidates=candidates, poll=poll)

@voting_bp.route('/submit', methods=['POST'])
@user_required
def submit_vote():
    """Submit vote"""
    poll_id_int = session.get('face_verified_poll_id')
    if not poll_id_int:
        return jsonify({'success': False, 'error': 'Poll selection required'}), 400
    poll = db.get_poll_by_id(poll_id_int)
    if not poll:
        return jsonify({'success': False, 'error': 'Invalid poll'}), 400

    # Check if poll is active
    if not db.is_poll_active(poll_id_int):
        return jsonify({'success': False, 'error': 'Polling is currently closed'}), 403

    # Check if face was verified
    if not session.get('face_verified') or session.get('face_verified_poll_id') != poll_id_int:
        return jsonify({'success': False, 'error': 'Face verification required'}), 403
    
    # Check if user already voted
    if db.has_user_voted(session['user_id'], poll_id_int):
        return jsonify({'success': False, 'error': 'You have already voted'}), 400
    
    # Get candidate ID from request
    data = request.get_json() if request.is_json else request.form
    candidate_id = data.get('candidate_id')
    
    if not candidate_id:
        return jsonify({'success': False, 'error': 'No candidate selected'}), 400

    try:
        candidate_id = int(candidate_id)
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid candidate selected'}), 400
    
    # Verify candidate exists and belongs to active poll
    candidate = db.get_candidate_by_id(candidate_id)
    if not candidate:
        return jsonify({'success': False, 'error': 'Invalid candidate selected'}), 400
    if candidate.get('poll_id') != poll_id_int:
        return jsonify({'success': False, 'error': 'Candidate does not belong to active poll'}), 400

    # Cast vote
    result = db.cast_vote(session['user_id'], candidate_id, poll_id_int)
    
    if result['success']:
        receipt_hash = None
        receipt_url = None
        # Generate acknowledgement slip
        try:
            user = db.get_user_by_id(session['user_id'])
            if user and candidate:
                pdf_gen = VoterReceiptPDFGenerator()
                voter_name = f"{user['first_name']} {user['last_name']}"
                receipt_result = pdf_gen.generate_receipt(
                    voter_name=voter_name,
                    aadhar_last4=user.get('aadhar_last4'),
                    candidate_name=candidate['candidate_name'],
                    party_name=candidate['party_name'],
                    user_id=user['id'],
                    candidate_id=candidate['id'],
                    receipt_url_base=request.host_url.rstrip('/')
                )
                if receipt_result['success']:
                    db.store_receipt(
                        user_id=user['id'],
                        candidate_id=candidate['id'],
                        poll_id=poll_id_int,
                        receipt_hash=receipt_result['receipt_hash'],
                        pdf_path=receipt_result['pdf_path']
                    )
                    receipt_hash = receipt_result['receipt_hash']
                    receipt_url = url_for('voting.get_receipt', receipt_hash=receipt_hash)
        except Exception:
            pass

        # Clear face verification
        session.pop('face_verified', None)
        session.pop('face_verified_poll_id', None)
        
        return jsonify({
            'success': True,
            'message': 'Vote submitted successfully',
            'redirect': url_for('user.home'),
            'receipt_hash': receipt_hash,
            'receipt_url': receipt_url
        })
    else:
        return jsonify({'success': False, 'error': result['error']}), 400

@voting_bp.route('/status')
@user_required
def voting_status():
    """Check if user has voted"""
    poll_id = request.args.get('poll_id')
    poll_id_int = int(poll_id) if poll_id and poll_id.isdigit() else None
    has_voted = db.has_user_voted(session['user_id'], poll_id_int) if poll_id_int else db.has_user_voted(session['user_id'])
    return jsonify({'has_voted': has_voted})

@voting_bp.route('/receipt/<receipt_hash>')
@user_required
def get_receipt(receipt_hash):
    """Download voter acknowledgement slip"""
    receipt = db.get_receipt_by_hash(receipt_hash)
    if not receipt:
        return jsonify({'success': False, 'error': 'Receipt not found'}), 404
    if receipt['user_id'] != session['user_id']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    if not receipt.get('pdf_path') or not os.path.exists(receipt['pdf_path']):
        return jsonify({'success': False, 'error': 'Receipt file missing'}), 404
    return send_file(receipt['pdf_path'], as_attachment=True)
