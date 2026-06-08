"""
Face Recognition Module
Uses DNN detection + grouped embedding matching for face verification
"""
from config import Config
from models.database import Database
from modules.face_capture import FaceCapture
from modules.face_embeddings import FaceEmbeddingHandler


class FaceRecognizer:
    def __init__(self):
        """Initialize face recognizer"""
        self.face_capture = FaceCapture()
        self.embedding_handler = FaceEmbeddingHandler()
        self.db = Database()

    def _build_error_message(self, reason, context='login'):
        messages = {
            'no_registered_faces': 'No enrolled faces found. Please contact admin.',
            'not_enrolled': 'Face login is not available for this voter. Please use email and password.' if context == 'login' else 'Face verification is not set up for this voter account.',
            'insufficient_frames': 'Not enough clear face frames were captured. Please try again.',
            'weak_match': 'Face match is too weak. Please use email and password.' if context == 'login' else 'Face verification failed for this account. Please try again.',
            'ambiguous_match': 'Face match is ambiguous. Please use email and password.' if context == 'login' else 'Face verification is ambiguous. Please try again.',
            'duplicate_face_detected': 'Face already registered with another voter.',
            'unable_to_extract': 'Unable to extract face features.'
        }
        return messages.get(reason, 'Unable to recognize face.')

    def _format_result(self, analysis, detection_confidences=None, context='login'):
        detection_confidences = detection_confidences or []
        response = {
            'success': analysis.get('success', False),
            'user_id': analysis.get('winner_user_id'),
            'confidence': analysis.get('winner_score', 0.0),
            'winner_user_id': analysis.get('winner_user_id'),
            'runner_up_user_id': analysis.get('runner_up_user_id'),
            'winner_score': analysis.get('winner_score', 0.0),
            'runner_up_score': analysis.get('runner_up_score', 0.0),
            'score_margin': analysis.get('score_margin', 0.0),
            'valid_frame_count': analysis.get('valid_frame_count', 0),
            'passing_frame_count': analysis.get('passing_frame_count', 0),
            'detection_confidence': max(detection_confidences) if detection_confidences else 0.0,
            'error_code': analysis.get('reason')
        }
        if not response['success']:
            response['error'] = self._build_error_message(analysis.get('reason'), context=context)
        return response

    def recognize_face_from_images(self, face_images, detection_confidences=None):
        """Recognize an enrolled voter from multiple cropped face images."""
        embeddings = self.embedding_handler.extract_embeddings(face_images)
        if len(embeddings) < Config.FACE_MIN_VALID_FRAMES:
            return self._format_result({
                'success': False,
                'reason': 'insufficient_frames',
                'winner_user_id': None,
                'runner_up_user_id': None,
                'winner_score': 0.0,
                'runner_up_score': 0.0,
                'score_margin': 0.0,
                'valid_frame_count': len(embeddings),
                'passing_frame_count': 0
            }, detection_confidences, context='login')

        existing_embeddings = self.db.get_all_face_embeddings()
        if not existing_embeddings:
            return self._format_result({
                'success': False,
                'reason': 'no_registered_faces',
                'winner_user_id': None,
                'runner_up_user_id': None,
                'winner_score': 0.0,
                'runner_up_score': 0.0,
                'score_margin': 0.0,
                'valid_frame_count': len(embeddings),
                'passing_frame_count': 0
            }, detection_confidences, context='login')

        analysis = self.embedding_handler.identify_user(embeddings, existing_embeddings)
        return self._format_result(analysis, detection_confidences, context='login')

    def verify_user_from_images(self, user_id, face_images, detection_confidences=None):
        """Verify a live face against a specific logged-in voter."""
        embeddings = self.embedding_handler.extract_embeddings(face_images)
        if len(embeddings) < Config.FACE_MIN_VALID_FRAMES:
            return self._format_result({
                'success': False,
                'reason': 'insufficient_frames',
                'winner_user_id': user_id,
                'runner_up_user_id': None,
                'winner_score': 0.0,
                'runner_up_score': 0.0,
                'score_margin': 0.0,
                'valid_frame_count': len(embeddings),
                'passing_frame_count': 0
            }, detection_confidences, context='vote')

        existing_embeddings = self.db.get_all_face_embeddings()
        analysis = self.embedding_handler.verify_user(user_id, embeddings, existing_embeddings)
        return self._format_result(analysis, detection_confidences, context='vote')

    def recognize_face_from_webcam(self):
        """Capture multiple face frames from webcam and identify the voter."""
        capture_result = self.face_capture.verify_face_realtime('Face Recognition')
        if capture_result is None:
            return {
                'success': False,
                'error': self._build_error_message('insufficient_frames', context='login'),
                'error_code': 'insufficient_frames',
                'confidence': 0.0,
                'winner_user_id': None,
                'runner_up_user_id': None,
                'winner_score': 0.0,
                'runner_up_score': 0.0,
                'score_margin': 0.0,
                'valid_frame_count': 0,
                'passing_frame_count': 0,
                'detection_confidence': 0.0
            }
        return self.recognize_face_from_images(
            capture_result['face_images'],
            detection_confidences=capture_result.get('detection_confidences')
        )

    def verify_user_from_webcam(self, user_id):
        """Capture multiple face frames from webcam and verify the logged-in voter."""
        capture_result = self.face_capture.verify_face_realtime('Face Verification')
        if capture_result is None:
            return {
                'success': False,
                'error': self._build_error_message('insufficient_frames', context='vote'),
                'error_code': 'insufficient_frames',
                'confidence': 0.0,
                'winner_user_id': user_id,
                'runner_up_user_id': None,
                'winner_score': 0.0,
                'runner_up_score': 0.0,
                'score_margin': 0.0,
                'valid_frame_count': 0,
                'passing_frame_count': 0,
                'detection_confidence': 0.0
            }
        return self.verify_user_from_images(
            user_id,
            capture_result['face_images'],
            detection_confidences=capture_result.get('detection_confidences')
        )

    def is_model_trained(self):
        """Compatibility method (legacy SVM model). Not used for recognition."""
        return False
