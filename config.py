"""
Configuration settings for the Facial Recognition Voting System
"""
import os

class Config:
    # Base directory
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    
    # Secret key for session management
    SECRET_KEY = os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production-2026')

    # Aadhaar hashing secret (HMAC)
    AADHAR_HASH_SECRET = os.environ.get('AADHAR_HASH_SECRET', 'change-me-aadhar-secret-2026')
    
    # Database configuration
    DATABASE_PATH = os.path.join(BASE_DIR, 'database', 'voting.db')
    
    # Upload folders
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    PARTY_SYMBOLS_FOLDER = os.path.join(UPLOAD_FOLDER, 'party_symbols')
    FACE_DATASET_FOLDER = os.path.join(UPLOAD_FOLDER, 'face_dataset')
    MANIFESTOS_FOLDER = os.path.join(UPLOAD_FOLDER, 'manifestos')
    RECEIPTS_FOLDER = os.path.join(UPLOAD_FOLDER, 'receipts')
    PROFILE_PHOTOS_FOLDER = os.path.join(UPLOAD_FOLDER, 'profile_photos')
    ID_CARDS_FOLDER = os.path.join(UPLOAD_FOLDER, 'id_cards')
    
    # Model configuration
    MODEL_PATH = os.path.join(BASE_DIR, 'models', 'face_recognition_model.pkl')
    HAAR_CASCADE_PATH = 'haarcascade_frontalface_default.xml'
    FACE_DETECTOR_PROTO = os.path.join(BASE_DIR, 'models', 'face_detector', 'deploy.prototxt')
    FACE_DETECTOR_MODEL = os.path.join(BASE_DIR, 'models', 'face_detector', 'res10_300x300_ssd_iter_140000.caffemodel')
    FACE_EMBEDDING_MODEL = os.path.join(BASE_DIR, 'models', 'face_embedding', 'openface_nn4.small2.v1.t7')

    # Face detection/recognition thresholds
    FACE_DETECT_CONFIDENCE = 0.7
    FACE_MIN_SIZE = 60  # Minimum face width/height in pixels
    FACE_IDENTIFY_THRESHOLD = 0.80
    FACE_VERIFY_THRESHOLD = 0.78
    FACE_DUPLICATE_THRESHOLD = 0.80
    FACE_MARGIN_THRESHOLD = 0.08
    FACE_MIN_VALID_FRAMES = 8
    FACE_ENROLLMENT_SAMPLE_LIMIT = 10
    FACE_MATCH_THRESHOLD = FACE_IDENTIFY_THRESHOLD
    
    # Admin credentials
    ADMIN_EMAIL = 'admin@voting.com'
    ADMIN_PASSWORD = 'admin'

    # Secret key required to register new admin accounts
    # Change this to a strong secret before deploying
    ADMIN_REGISTRATION_SECRET = os.environ.get('ADMIN_REGISTRATION_SECRET', 'VoteGuard-admin-2026')
    
    # Face capture settings
    FACE_SAMPLES = 50  # Number of face samples to capture per user

    # Prefer browser-based capture for Mac + mobile
    USE_BROWSER_FACE_CAPTURE = True

    # Liveness detection configuration
    LIVENESS_ENABLED = os.environ.get('LIVENESS_ENABLED', 'true').lower() in ('1', 'true', 'yes')
    LIVENESS_CHALLENGE_WINDOW_SECONDS = int(os.environ.get('LIVENESS_CHALLENGE_WINDOW_SECONDS', '8'))
    LIVENESS_SUBMISSION_GRACE_SECONDS = int(os.environ.get('LIVENESS_SUBMISSION_GRACE_SECONDS', '12'))
    LIVENESS_MIN_FACE_FRAMES = int(os.environ.get('LIVENESS_MIN_FACE_FRAMES', '6'))
    LIVENESS_MIN_FRAME_VARIATION = float(os.environ.get('LIVENESS_MIN_FRAME_VARIATION', '2.0'))
    LIVENESS_MIN_FACE_MOVEMENT = float(os.environ.get('LIVENESS_MIN_FACE_MOVEMENT', '0.03'))
    LIVENESS_HEAD_TURN_THRESHOLD = float(os.environ.get('LIVENESS_HEAD_TURN_THRESHOLD', '0.08'))
    LIVENESS_WEBCAM_WINDOW_SECONDS = int(os.environ.get('LIVENESS_WEBCAM_WINDOW_SECONDS', '5'))

    # Enable HTTPS for mobile camera access if needed
    ENABLE_HTTPS = os.environ.get('ENABLE_HTTPS', 'false').lower() in ('1', 'true', 'yes')
    
    # Face similarity threshold for duplicate detection
    FACE_SIMILARITY_THRESHOLD = FACE_DUPLICATE_THRESHOLD

    # Allow admin override for duplicate face registration
    ALLOW_DUPLICATE_FACE_OVERRIDE = False
    
    # Allowed extensions for uploads
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
    ALLOWED_PDF_EXTENSIONS = {'pdf'}
    ALLOWED_CSV_EXTENSIONS = {'csv'}
    MAX_VOTER_CSV_SIZE_BYTES = 10 * 1024 * 1024
    
    @staticmethod
    def init_app(app):
        """Initialize application with config"""
        # Create necessary directories
        os.makedirs(Config.DATABASE_PATH.rsplit(os.sep, 1)[0], exist_ok=True)
        os.makedirs(Config.PARTY_SYMBOLS_FOLDER, exist_ok=True)
        os.makedirs(Config.FACE_DATASET_FOLDER, exist_ok=True)
        os.makedirs(Config.MANIFESTOS_FOLDER, exist_ok=True)
        os.makedirs(Config.RECEIPTS_FOLDER, exist_ok=True)
        os.makedirs(Config.PROFILE_PHOTOS_FOLDER, exist_ok=True)
        os.makedirs(Config.ID_CARDS_FOLDER, exist_ok=True)
        os.makedirs(os.path.join(Config.BASE_DIR, 'models'), exist_ok=True)
        os.makedirs(os.path.join(Config.BASE_DIR, 'models', 'face_detector'), exist_ok=True)
        os.makedirs(os.path.join(Config.BASE_DIR, 'models', 'face_embedding'), exist_ok=True)
