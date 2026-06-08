"""
Face Embeddings Module
Handles face embedding extraction and comparison for duplicate detection
"""
import hashlib
import json
import os
import statistics

import cv2
import numpy as np

from config import Config


class FaceEmbeddingHandler:
    def __init__(self):
        """Initialize face embedding handler"""
        self.similarity_threshold = Config.FACE_SIMILARITY_THRESHOLD
        self.embedder = self._load_embedder()

    def _load_embedder(self):
        model_path = Config.FACE_EMBEDDING_MODEL
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                "Face embedding model file not found. "
                "Place openface_nn4.small2.v1.t7 under models/face_embedding/."
            )
        return cv2.dnn.readNetFromTorch(model_path)

    def _normalize_embedding(self, embedding):
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding.astype('float32')

    def _to_array(self, embedding):
        if isinstance(embedding, str):
            embedding = json.loads(embedding)
        return np.asarray(embedding, dtype='float32')

    def extract_embeddings(self, face_images, limit=None):
        """Extract normalized embeddings from multiple BGR face images."""
        embeddings = []
        for img in face_images:
            if img is None:
                continue
            face = cv2.resize(img, (96, 96))
            blob = cv2.dnn.blobFromImage(
                face,
                scalefactor=1.0 / 255,
                size=(96, 96),
                mean=(0, 0, 0),
                swapRB=True,
                crop=False
            )
            self.embedder.setInput(blob)
            vec = self.embedder.forward()
            embedding = self._normalize_embedding(vec.flatten().astype('float32'))
            embeddings.append(embedding)
            if limit and len(embeddings) >= limit:
                break
        return embeddings

    def extract_embedding(self, face_images):
        """Extract an averaged embedding for compatibility with legacy code."""
        embeddings = self.extract_embeddings(face_images)
        if not embeddings:
            return None
        averaged = np.mean(np.vstack(embeddings), axis=0)
        return self._normalize_embedding(averaged)

    def extract_embedding_from_image(self, face_image):
        """Extract embedding from a single BGR face image."""
        embeddings = self.extract_embeddings([face_image], limit=1)
        return embeddings[0] if embeddings else None

    def compare_embeddings(self, embedding1, embedding2):
        """Compare two face embeddings using cosine similarity."""
        emb1 = self._to_array(embedding1)
        emb2 = self._to_array(embedding2)

        dot_product = float(np.dot(emb1, emb2))
        norm1 = float(np.linalg.norm(emb1))
        norm2 = float(np.linalg.norm(emb2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot_product / (norm1 * norm2)

    def generate_face_id(self, embedding, salt=None):
        """Generate a stable-enough face ID for database storage."""
        rounded = np.round(self._to_array(embedding), 4)
        payload = rounded.tobytes()
        if salt is not None:
            payload += str(salt).encode('utf-8')
        return hashlib.sha256(payload).hexdigest()

    def group_embeddings_by_user(self, existing_embeddings, allowed_user_ids=None):
        grouped = {}
        allowed = set(allowed_user_ids) if allowed_user_ids is not None else None
        for existing in existing_embeddings:
            user_id = existing['user_id']
            if allowed is not None and user_id not in allowed:
                continue
            grouped.setdefault(user_id, []).append(self._to_array(existing['embedding_data']))
        return grouped

    def _score_user(self, live_embeddings, enrolled_embeddings, threshold, top_k=5):
        frame_scores = []
        for live_embedding in live_embeddings:
            if not enrolled_embeddings:
                continue
            best_frame_score = max(
                self.compare_embeddings(live_embedding, enrolled_embedding)
                for enrolled_embedding in enrolled_embeddings
            )
            frame_scores.append(float(best_frame_score))

        ranked_scores = sorted(frame_scores, reverse=True)
        top_scores = ranked_scores[:min(top_k, len(ranked_scores))]
        aggregate_score = float(statistics.median(top_scores)) if top_scores else 0.0
        passing_frames = sum(score >= threshold for score in frame_scores)
        return {
            'aggregate_score': aggregate_score,
            'frame_scores': frame_scores,
            'passing_frames': passing_frames
        }

    def _summarize_candidates(self, live_embeddings, grouped_embeddings, threshold):
        candidates = []
        for user_id, enrolled_embeddings in grouped_embeddings.items():
            summary = self._score_user(live_embeddings, enrolled_embeddings, threshold)
            candidates.append({
                'user_id': user_id,
                'score': summary['aggregate_score'],
                'passing_frames': summary['passing_frames'],
                'frame_scores': summary['frame_scores']
            })
        candidates.sort(key=lambda item: item['score'], reverse=True)
        return candidates

    def identify_user(self, live_embeddings, existing_embeddings, threshold=None, margin_threshold=None, min_valid_frames=None):
        """Identify the best matching user from enrolled embeddings."""
        threshold = Config.FACE_IDENTIFY_THRESHOLD if threshold is None else threshold
        margin_threshold = Config.FACE_MARGIN_THRESHOLD if margin_threshold is None else margin_threshold
        min_valid_frames = Config.FACE_MIN_VALID_FRAMES if min_valid_frames is None else min_valid_frames

        valid_frame_count = len(live_embeddings)
        if valid_frame_count < min_valid_frames:
            return {
                'success': False,
                'reason': 'insufficient_frames',
                'winner_user_id': None,
                'runner_up_user_id': None,
                'winner_score': 0.0,
                'runner_up_score': 0.0,
                'score_margin': 0.0,
                'valid_frame_count': valid_frame_count,
                'passing_frame_count': 0,
                'candidates': []
            }

        grouped = self.group_embeddings_by_user(existing_embeddings)
        if not grouped:
            return {
                'success': False,
                'reason': 'not_enrolled',
                'winner_user_id': None,
                'runner_up_user_id': None,
                'winner_score': 0.0,
                'runner_up_score': 0.0,
                'score_margin': 0.0,
                'valid_frame_count': valid_frame_count,
                'passing_frame_count': 0,
                'candidates': []
            }

        candidates = self._summarize_candidates(live_embeddings, grouped, threshold)
        winner = candidates[0] if candidates else None
        runner_up = candidates[1] if len(candidates) > 1 else None
        winner_score = winner['score'] if winner else 0.0
        runner_up_score = runner_up['score'] if runner_up else 0.0
        score_margin = winner_score - runner_up_score
        passing_frame_count = winner['passing_frames'] if winner else 0

        if not winner or winner_score < threshold or passing_frame_count < 5:
            reason = 'weak_match'
            success = False
        elif score_margin < margin_threshold:
            reason = 'ambiguous_match'
            success = False
        else:
            reason = None
            success = True

        return {
            'success': success,
            'reason': reason,
            'winner_user_id': winner['user_id'] if winner else None,
            'runner_up_user_id': runner_up['user_id'] if runner_up else None,
            'winner_score': float(winner_score),
            'runner_up_score': float(runner_up_score),
            'score_margin': float(score_margin),
            'valid_frame_count': valid_frame_count,
            'passing_frame_count': passing_frame_count,
            'candidates': candidates
        }

    def verify_user(self, target_user_id, live_embeddings, existing_embeddings, threshold=None, margin_threshold=None, min_valid_frames=None):
        """Verify that live embeddings belong to a specific enrolled user."""
        threshold = Config.FACE_VERIFY_THRESHOLD if threshold is None else threshold
        margin_threshold = Config.FACE_MARGIN_THRESHOLD if margin_threshold is None else margin_threshold
        min_valid_frames = Config.FACE_MIN_VALID_FRAMES if min_valid_frames is None else min_valid_frames

        valid_frame_count = len(live_embeddings)
        if valid_frame_count < min_valid_frames:
            return {
                'success': False,
                'reason': 'insufficient_frames',
                'winner_user_id': target_user_id,
                'runner_up_user_id': None,
                'winner_score': 0.0,
                'runner_up_score': 0.0,
                'score_margin': 0.0,
                'valid_frame_count': valid_frame_count,
                'passing_frame_count': 0
            }

        grouped = self.group_embeddings_by_user(existing_embeddings)
        target_embeddings = grouped.get(target_user_id)
        if not target_embeddings:
            return {
                'success': False,
                'reason': 'not_enrolled',
                'winner_user_id': target_user_id,
                'runner_up_user_id': None,
                'winner_score': 0.0,
                'runner_up_score': 0.0,
                'score_margin': 0.0,
                'valid_frame_count': valid_frame_count,
                'passing_frame_count': 0
            }

        target_summary = self._score_user(live_embeddings, target_embeddings, threshold)
        other_candidates = self._summarize_candidates(
            live_embeddings,
            {user_id: embeddings for user_id, embeddings in grouped.items() if user_id != target_user_id},
            threshold
        )
        runner_up = other_candidates[0] if other_candidates else None
        target_score = target_summary['aggregate_score']
        runner_up_score = runner_up['score'] if runner_up else 0.0
        score_margin = target_score - runner_up_score
        passing_frame_count = target_summary['passing_frames']

        if target_score < threshold or passing_frame_count < 5:
            reason = 'weak_match'
            success = False
        elif score_margin < margin_threshold:
            reason = 'ambiguous_match'
            success = False
        else:
            reason = None
            success = True

        return {
            'success': success,
            'reason': reason,
            'winner_user_id': target_user_id,
            'runner_up_user_id': runner_up['user_id'] if runner_up else None,
            'winner_score': float(target_score),
            'runner_up_score': float(runner_up_score),
            'score_margin': float(score_margin),
            'valid_frame_count': valid_frame_count,
            'passing_frame_count': passing_frame_count
        }

    def check_duplicate(self, new_embeddings, existing_embeddings):
        """Check whether newly captured embeddings already match an enrolled user."""
        if new_embeddings is None:
            embeddings = []
        elif isinstance(new_embeddings, np.ndarray) and new_embeddings.ndim == 1:
            embeddings = [new_embeddings]
        elif isinstance(new_embeddings, list):
            embeddings = [self._to_array(embedding) for embedding in new_embeddings]
        else:
            embeddings = [self._to_array(new_embeddings)]

        analysis = self.identify_user(
            embeddings,
            existing_embeddings,
            threshold=Config.FACE_DUPLICATE_THRESHOLD,
            margin_threshold=Config.FACE_MARGIN_THRESHOLD,
            min_valid_frames=Config.FACE_MIN_VALID_FRAMES
        )
        return {
            'is_duplicate': analysis['success'],
            'matched_user_id': analysis['winner_user_id'],
            'similarity': analysis['winner_score'],
            'analysis': analysis
        }

    def match_best_embedding(self, new_embedding, existing_embeddings):
        """Legacy single-embedding best-match helper."""
        best = {
            'user_id': None,
            'similarity': 0.0
        }
        for existing in existing_embeddings:
            similarity = self.compare_embeddings(new_embedding, existing['embedding_data'])
            if similarity > best['similarity']:
                best = {
                    'user_id': existing['user_id'],
                    'similarity': similarity
                }
        return best

    def load_face_images(self, user_id):
        """Load face images for a user from dataset folder."""
        face_folder = os.path.join(Config.FACE_DATASET_FOLDER, f"user_{user_id}")
        if not os.path.exists(face_folder):
            return []

        face_images = []
        for filename in sorted(os.listdir(face_folder)):
            if filename.endswith(('.jpg', '.jpeg', '.png')):
                img_path = os.path.join(face_folder, filename)
                img = cv2.imread(img_path)
                if img is not None:
                    face_images.append(img)
        return face_images
