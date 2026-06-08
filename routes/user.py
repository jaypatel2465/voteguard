"""
User Routes
Handles user registration, login, profile management
"""
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app, send_file
from functools import wraps
from models.database import Database
from modules.face_capture import FaceCapture
from modules.face_recognition import FaceRecognizer
from modules.security import hash_aadhar, mask_aadhar, generate_eid_hash
from config import Config
import os
import re
import shutil
import cv2
import numpy as np
from modules.face_detector import FaceDetector
from modules.face_embeddings import FaceEmbeddingHandler
from modules.id_card_generator import VoterIDCardGenerator
from modules.liveness import issue_liveness_challenge, validate_liveness_submission, verify_webcam_liveness
from werkzeug.utils import secure_filename

user_bp = Blueprint('user', __name__, url_prefix='/user')
db = Database()

def _decode_image(file_storage):
    """Decode uploaded file into BGR image"""
    if not file_storage:
        return None
    data = file_storage.read()
    if not data:
        return None
    nparr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    return img


def _clear_face_dataset(user_id):
    user_folder = os.path.join(Config.FACE_DATASET_FOLDER, f'user_{user_id}')
    if os.path.exists(user_folder):
        shutil.rmtree(user_folder)
    return user_folder


def _save_face_dataset(user_id, face_images, limit=None):
    user_folder = _clear_face_dataset(user_id)
    os.makedirs(user_folder, exist_ok=True)
    sample_limit = limit or Config.FACE_ENROLLMENT_SAMPLE_LIMIT
    for index, face_image in enumerate(face_images[:sample_limit]):
        img_path = os.path.join(user_folder, f'face_{index}.jpg')
        cv2.imwrite(img_path, cv2.resize(face_image, (100, 100)))
    return user_folder


def _collect_face_images(files):
    detector = FaceDetector()
    face_images = []
    detection_confidences = []
    for frame in files:
        try:
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
        except Exception:
            current_app.logger.exception('Failed to process frame')
    return face_images, detection_confidences


def _log_face_result(result, context, user_id=None):
    db.log_face_attempt(
        user_id=user_id if user_id is not None else result.get('user_id'),
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
        valid_frame_count=result.get('valid_frame_count')
    )


def _face_capture_redirect():
    return url_for('user.login') if session.get('temp_user_id') else url_for('user.home')


def _face_capture_message():
    return 'Face enrollment updated successfully.' if session.get('user_logged_in') and not session.get('temp_user_id') else 'Registration successful. Face enrollment completed.'


def _refresh_eid_assets(user_id, receipt_url_base, force_regenerate=False):
    user = db.get_user_by_id(user_id)
    if not user or not user.get('profile_image'):
        return user

    eid_hash = user.get('eid_hash') or generate_eid_hash(user['id'], user.get('aadhar_hash'))
    pdf_path = user.get('eid_pdf_path')
    needs_generation = force_regenerate or not eid_hash or not pdf_path or not os.path.exists(pdf_path)

    if not needs_generation:
        return user

    pdf_gen = VoterIDCardGenerator()
    card_result = pdf_gen.generate_id_card(user, eid_hash, receipt_url_base=receipt_url_base)
    if card_result.get('success'):
        db.update_user(
            user_id,
            eid_hash=eid_hash,
            eid_pdf_path=card_result['pdf_path']
        )
        return db.get_user_by_id(user_id)
    return user


def user_required(f):
    """Decorator to require user login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_logged_in' not in session:
            return redirect(url_for('user.login'))
        return f(*args, **kwargs)
    return decorated_function

def validate_aadhar(aadhar):
    """Validate Aadhaar ID (12 digits)"""
    return bool(re.match(r'^\d{12}$', aadhar))

def validate_phone(phone):
    """Validate phone number (10 digits)"""
    return bool(re.match(r'^\d{10}$', phone))

def validate_email(email):
    """Validate email format"""
    return bool(re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email))

@user_bp.route('/register', methods=['GET', 'POST'])
def register():
    """User registration page"""
    if request.method == 'GET':
        return render_template('user/register.html')
    
    # POST request - handle registration
    data = request.form if request.form else request.get_json() or {}
    
    first_name = data.get('first_name')
    middle_name = data.get('middle_name', '')
    last_name = data.get('last_name')
    aadhar_id = data.get('aadhar_id')
    phone = data.get('phone')
    email = data.get('email')
    password = data.get('password')
    profile_image = request.files.get('profile_image')
    
    # Validate inputs
    if not all([first_name, last_name, aadhar_id, phone, email, password]):
        return jsonify({'success': False, 'error': 'All required fields must be filled'}), 400

    if not profile_image or not profile_image.filename:
        return jsonify({'success': False, 'error': 'Profile photo is required for E-ID'}), 400
    
    if not validate_aadhar(aadhar_id):
        return jsonify({'success': False, 'error': 'Aadhaar ID must be 12 digits'}), 400
    
    if not validate_phone(phone):
        return jsonify({'success': False, 'error': 'Phone number must be 10 digits'}), 400
    
    if not validate_email(email):
        return jsonify({'success': False, 'error': 'Invalid email format'}), 400
    
    # Check if email already exists
    existing_user = db.get_user_by_email(email)
    if existing_user:
        return jsonify({'success': False, 'error': 'Already registered as voter'}), 400

    # Hash Aadhaar and check duplicate Aadhaar
    aadhar_hash = hash_aadhar(aadhar_id)
    existing_aadhar = db.get_user_by_aadhar_hash(aadhar_hash)
    if existing_aadhar:
        return jsonify({'success': False, 'error': 'Aadhaar already registered'}), 400
    
    # Validate profile image
    if not ('.' in profile_image.filename and profile_image.filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS):
        return jsonify({'success': False, 'error': 'Invalid profile image file type'}), 400

    # Create user in database
    aadhar_last4 = str(aadhar_id)[-4:]
    result = db.create_user(first_name, middle_name, last_name, aadhar_hash, aadhar_last4, phone, email, password)
    
    if result['success']:
        # Save profile image
        filename = secure_filename(profile_image.filename)
        safe_name = f"user_{result['user_id']}_{filename}"
        file_path = os.path.join(Config.PROFILE_PHOTOS_FOLDER, safe_name)
        profile_image.save(file_path)
        db.update_user(result['user_id'], profile_image=safe_name)

        # Generate E-ID card
        try:
            _refresh_eid_assets(result['user_id'], request.host_url.rstrip('/'), force_regenerate=True)
        except Exception:
            pass

        # Store user ID in session for face capture
        session['temp_user_id'] = result['user_id']
        session['temp_aadhar_last4'] = aadhar_last4
        return jsonify({
            'success': True,
            'message': 'Registration successful. Please capture your face images.',
            'redirect': url_for('user.face_capture')
        })
    else:
        return jsonify({'success': False, 'error': result['error']}), 400

@user_bp.route('/face-capture', methods=['GET'])
def face_capture():
    """Face capture page"""
    user_id = session.get('temp_user_id') or session.get('user_id')
    
    if not user_id:
        return redirect(url_for('user.register'))
    
    aadhar_last4 = session.get('temp_aadhar_last4')
    masked = mask_aadhar(aadhar_last4)
    return render_template('user/face_capture.html', masked_aadhar=masked)

@user_bp.route('/capture-face', methods=['POST'])
def capture_face_process():
    """Process webcam face capture with duplicate detection and multi-sample enrollment."""
    user_id = session.get('temp_user_id') or session.get('user_id')
    is_registration = bool(session.get('temp_user_id'))

    if not user_id:
        return jsonify({'success': False, 'error': 'No user in session'}), 400

    try:
        face_capture = FaceCapture()
        embedding_handler = FaceEmbeddingHandler()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    result = face_capture.capture_faces(user_id)
    if not result['success']:
        return jsonify({'success': False, 'error': result['error'], 'error_code': 'insufficient_frames'}), 400

    face_images = embedding_handler.load_face_images(user_id)
    enrollment_embeddings = embedding_handler.extract_embeddings(
        face_images,
        limit=Config.FACE_ENROLLMENT_SAMPLE_LIMIT
    )
    if len(enrollment_embeddings) < Config.FACE_MIN_VALID_FRAMES:
        _clear_face_dataset(user_id)
        return jsonify({
            'success': False,
            'error': 'Insufficient clear face frames. Please try again.',
            'error_code': 'insufficient_frames'
        }), 400

    existing_embeddings = [
        embedding for embedding in db.get_all_face_embeddings()
        if embedding.get('user_id') != user_id
    ]
    duplicate_check = embedding_handler.check_duplicate(enrollment_embeddings, existing_embeddings)
    if duplicate_check['is_duplicate']:
        _clear_face_dataset(user_id)
        if is_registration and not Config.ALLOW_DUPLICATE_FACE_OVERRIDE:
            db.delete_user(user_id)
        return jsonify({
            'success': False,
            'error': 'Face already registered with another Aadhaar ID. Duplicate registrations are not allowed.',
            'error_code': 'duplicate_face_detected',
            'matched_user_id': duplicate_check['matched_user_id']
        }), 400

    store_result = db.replace_face_embeddings(user_id, enrollment_embeddings)
    if not store_result['success']:
        return jsonify({'success': False, 'error': store_result['error']}), 500

    session.pop('temp_user_id', None)
    session.pop('temp_aadhar_last4', None)
    return jsonify({
        'success': True,
        'message': _face_capture_message(),
        'redirect': _face_capture_redirect()
    })


@user_bp.route('/capture-face-web', methods=['POST'])
def capture_face_web():
    """Process browser-based face capture with strict duplicate checks."""
    user_id = session.get('temp_user_id') or session.get('user_id')
    is_registration = bool(session.get('temp_user_id'))

    if not user_id:
        return jsonify({'success': False, 'error': 'No user in session'}), 400

    frames = request.files.getlist('frames')
    if not frames:
        return jsonify({'success': False, 'error': 'No frames uploaded'}), 400

    try:
        embedding_handler = FaceEmbeddingHandler()
        face_images, _ = _collect_face_images(frames)
    except Exception as e:
        current_app.logger.exception('Failed to initialize face modules')
        return jsonify({'success': False, 'error': str(e)}), 500

    enrollment_embeddings = embedding_handler.extract_embeddings(
        face_images,
        limit=Config.FACE_ENROLLMENT_SAMPLE_LIMIT
    )
    if len(enrollment_embeddings) < Config.FACE_MIN_VALID_FRAMES:
        return jsonify({
            'success': False,
            'error': 'Insufficient clear face frames. Please try again.',
            'error_code': 'insufficient_frames'
        }), 400

    existing_embeddings = [
        embedding for embedding in db.get_all_face_embeddings()
        if embedding.get('user_id') != user_id
    ]
    duplicate_check = embedding_handler.check_duplicate(enrollment_embeddings, existing_embeddings)
    if duplicate_check['is_duplicate']:
        if is_registration and not Config.ALLOW_DUPLICATE_FACE_OVERRIDE:
            db.delete_user(user_id)
        return jsonify({
            'success': False,
            'error': 'Face already registered with another Aadhaar ID. Duplicate registrations are not allowed.',
            'error_code': 'duplicate_face_detected',
            'matched_user_id': duplicate_check['matched_user_id']
        }), 400

    _save_face_dataset(user_id, face_images, limit=len(enrollment_embeddings))
    store_result = db.replace_face_embeddings(user_id, enrollment_embeddings)
    if not store_result['success']:
        _clear_face_dataset(user_id)
        return jsonify({'success': False, 'error': store_result['error']}), 500

    session.pop('temp_user_id', None)
    session.pop('temp_aadhar_last4', None)

    return jsonify({
        'success': True,
        'message': _face_capture_message(),
        'redirect': _face_capture_redirect()
    })


@user_bp.route('/login', methods=['GET', 'POST'])
def login():

    """User login page"""
    if request.method == 'GET':
        return render_template('user/login.html')
    
    # POST request - handle login
    data = request.get_json() if request.is_json else request.form
    login_method = data.get('method', 'password')
    
    if login_method == 'password':
        # Email/Password login
        email = data.get('email')
        password = data.get('password')
        
        user = db.verify_user_password(email, password)
        
        if user:
            session['user_logged_in'] = True
            session['user_id'] = user['id']
            session['user_email'] = user['email']
            session['user_aadhar_last4'] = user.get('aadhar_last4')
            
            return jsonify({'success': True, 'redirect': url_for('user.home')})
        else:
            return jsonify({'success': False, 'error': 'Invalid credentials'}), 401
    
    elif login_method == 'face':
        if Config.USE_BROWSER_FACE_CAPTURE:
            return jsonify({'success': False, 'error': 'Browser-based face login is enabled.'}), 400

        try:
            liveness_result = verify_webcam_liveness('login')
            if not liveness_result['success']:
                return jsonify({
                    'success': False,
                    'error': liveness_result['error'],
                    'error_code': liveness_result.get('error_code')
                }), 400
            recognizer = FaceRecognizer()
            result = recognizer.recognize_face_from_webcam()
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

        _log_face_result(result, 'login')

        if result['success']:
            user = db.get_user_by_id(result['user_id'])
            if not user:
                return jsonify({'success': False, 'error': 'User not found', 'error_code': 'user_not_found'}), 404

            session['user_logged_in'] = True
            session['user_id'] = user['id']
            session['user_email'] = user['email']
            session['user_aadhar_last4'] = user.get('aadhar_last4')

            return jsonify({
                'success': True,
                'user_id': result['user_id'],
                'confidence': result['confidence'],
                'redirect': url_for('user.home')
            })

        return jsonify({
            'success': False,
            'error': result['error'],
            'error_code': result.get('error_code'),
            'fallback_to_password': result.get('error_code') in {'weak_match', 'ambiguous_match', 'not_enrolled', 'insufficient_frames'}
        }), 400


@user_bp.route('/liveness-challenge', methods=['POST'])
def user_liveness_challenge():
    """Issue a login liveness challenge for browser-based face login."""
    if not Config.LIVENESS_ENABLED:
        return jsonify({
            'success': True,
            'enabled': False,
            'message': 'Liveness detection is disabled.'
        })

    challenge = issue_liveness_challenge(session, context='login', browser_mode=True)
    return jsonify({
        'success': True,
        'enabled': True,
        'challenge_id': challenge['id'],
        'action': challenge['action'],
        'prompt': challenge['prompt'],
        'expires_at': challenge['expires_at']
    })

@user_bp.route('/login-face-web', methods=['POST'])
def login_face_web():
    """Browser-based face login using grouped multi-frame matching."""
    frames = request.files.getlist('frames')
    if not frames:
        file = request.files.get('frame')
        frames = [file] if file else []
    if not frames:
        return jsonify({'success': False, 'error': 'No image uploaded'}), 400

    liveness_result = validate_liveness_submission(
        session,
        'login',
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

    result = recognizer.recognize_face_from_images(face_images, detection_confidences=detection_confidences)
    _log_face_result(result, 'login')

    if not result['success']:
        return jsonify({
            'success': False,
            'error': result['error'],
            'error_code': result.get('error_code'),
            'fallback_to_password': result.get('error_code') in {'weak_match', 'ambiguous_match', 'not_enrolled', 'insufficient_frames'}
        }), 400

    user = db.get_user_by_id(result['user_id'])
    if not user:
        return jsonify({'success': False, 'error': 'User not found', 'error_code': 'user_not_found'}), 404

    session['user_logged_in'] = True
    session['user_id'] = user['id']
    session['user_email'] = user['email']
    session['user_aadhar_last4'] = user.get('aadhar_last4')

    return jsonify({
        'success': True,
        'user_id': user['id'],
        'confidence': result['confidence'],
        'redirect': url_for('user.home')
    })


@user_bp.route('/home')
@user_required
def home():
    """User homepage - shows only assigned polls"""
    user = db.get_user_by_email(session['user_email'])
    masked_aadhar = mask_aadhar(user.get('aadhar_last4'))
    # Only fetch polls this user is assigned to
    polls = db.get_polls_for_user(session['user_id'])
    poll_items = []
    for poll in polls:
        info = db.get_poll_status_info(poll)
        poll.update(info)
        poll['has_voted'] = db.has_user_voted(session['user_id'], poll['id'])
        poll['receipt'] = db.get_receipt_for_user_poll(session['user_id'], poll['id'])
        poll_items.append(poll)
    return render_template(
        'user/home.html',
        user=user,
        masked_aadhar=masked_aadhar,
        polls=poll_items
    )

@user_bp.route('/update-details', methods=['GET', 'POST'])
@user_required
def update_details():
    """Update user details"""
    if request.method == 'GET':
        user = db.get_user_by_email(session['user_email'])
        masked_aadhar = mask_aadhar(user.get('aadhar_last4'))
        return render_template('user/update_details.html', user=user, masked_aadhar=masked_aadhar)
    
    # POST request - handle update
    data = request.form if request.form else request.get_json() or {}
    
    # Build update fields
    update_fields = {}
    
    if data.get('first_name'):
        update_fields['first_name'] = data['first_name']
    if data.get('middle_name'):
        update_fields['middle_name'] = data['middle_name']
    if data.get('last_name'):
        update_fields['last_name'] = data['last_name']
    if data.get('phone'):
        if not validate_phone(data['phone']):
            return jsonify({'success': False, 'error': 'Invalid phone number'}), 400
        update_fields['phone'] = data['phone']
    if data.get('password'):
        update_fields['password'] = data['password']

    # Handle profile image upload
    profile_image = request.files.get('profile_image')
    if profile_image and profile_image.filename:
        if not ('.' in profile_image.filename and profile_image.filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS):
            return jsonify({'success': False, 'error': 'Invalid profile image file type'}), 400
        filename = secure_filename(profile_image.filename)
        safe_name = f"user_{session['user_id']}_{filename}"
        file_path = os.path.join(Config.PROFILE_PHOTOS_FOLDER, safe_name)
        profile_image.save(file_path)
        update_fields['profile_image'] = safe_name
    
    # Update user
    result = db.update_user(session['user_id'], **update_fields)
    
    if result['success']:
        # Generate/Update E-ID card if profile image exists
        _refresh_eid_assets(session['user_id'], request.host_url.rstrip('/'), force_regenerate=True)
        return jsonify({'success': True, 'message': 'Details updated successfully'})
    else:
        return jsonify({'success': False, 'error': 'Failed to update details'}), 400

@user_bp.route('/id-card', methods=['GET'])
@user_required
def id_card_download():
    """Download voter E-ID card"""
    user = _refresh_eid_assets(session['user_id'], request.host_url.rstrip('/'), force_regenerate=True)
    if not user or not user.get('eid_pdf_path'):
        return jsonify({'success': False, 'error': 'E-ID card not available'}), 404
    if not os.path.exists(user['eid_pdf_path']):
        return jsonify({'success': False, 'error': 'E-ID file missing'}), 404
    return send_file(user['eid_pdf_path'], as_attachment=True)


@user_bp.route('/logout')
def logout():
    """User logout - always redirect to home page"""
    session.clear()
    return redirect(url_for('index'))
