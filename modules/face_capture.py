"""
Face Capture Module using OpenCV and DNN face detection
Captures face images during user registration and verification
"""
import os
import shutil
import sys

import cv2
import numpy as np

from config import Config
from modules.face_detector import FaceDetector


class FaceCapture:
    def __init__(self):
        """Initialize face capture with DNN detector."""
        self.detector = FaceDetector()

    def capture_faces(self, user_id, num_samples=None):
        """Capture face images for a user and replace any previous enrollment dataset."""
        if num_samples is None:
            num_samples = Config.FACE_SAMPLES

        user_folder = os.path.join(Config.FACE_DATASET_FOLDER, f"user_{user_id}")
        if os.path.exists(user_folder):
            shutil.rmtree(user_folder)
        os.makedirs(user_folder, exist_ok=True)

        if sys.platform == 'darwin':
            cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
        else:
            cap = cv2.VideoCapture(0)

        if not cap.isOpened():
            return {
                'success': False,
                'error': 'Unable to access webcam'
            }

        count = 0
        captured_count = 0

        print(f"Starting face capture for User ID: {user_id}")
        print(f"Please look at the camera. Capturing {num_samples} images...")

        while captured_count < num_samples:
            ret, frame = cap.read()
            if not ret:
                break

            bbox, conf = self.detector.detect_best_face(frame)
            if bbox is not None:
                x, y, w, h = bbox
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.putText(
                    frame,
                    f'Face: {conf:.2f}',
                    (x, y - 10 if y - 10 > 10 else y + 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2
                )

                if count % 3 == 0:
                    face_img = frame[y:y + h, x:x + w]
                    face_img = cv2.resize(face_img, (100, 100))
                    img_path = os.path.join(user_folder, f'face_{captured_count}.jpg')
                    cv2.imwrite(img_path, face_img)
                    captured_count += 1
                    cv2.putText(
                        frame,
                        f'Captured: {captured_count}/{num_samples}',
                        (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        1,
                        (0, 255, 0),
                        2
                    )

            cv2.imshow('Face Capture - Press Q to quit early', frame)
            count += 1
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == 27:
                break

        cap.release()
        cv2.destroyAllWindows()

        if captured_count >= num_samples * 0.8:
            return {
                'success': True,
                'message': f'Successfully captured {captured_count} face images',
                'count': captured_count
            }
        return {
            'success': False,
            'error': f'Only captured {captured_count} images. Please try again.',
            'count': captured_count
        }

    def verify_face_realtime(self, display_text='Face Verification', target_samples=None, max_attempts=None):
        """Capture a burst of face crops for login or vote verification."""
        target_samples = target_samples or max(Config.FACE_MIN_VALID_FRAMES + 2, 10)
        max_attempts = max_attempts or target_samples * 3

        if sys.platform == 'darwin':
            cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
        else:
            cap = cv2.VideoCapture(0)

        if not cap.isOpened():
            return None

        print(f"{display_text} - Press SPACE to start capture, Q to cancel")

        face_images = []
        detection_confidences = []
        collecting = False
        attempts = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            bbox, conf = self.detector.detect_best_face(frame)
            if bbox is not None:
                x, y, w, h = bbox
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.putText(
                    frame,
                    f'Face: {conf:.2f}',
                    (x, y - 10 if y - 10 > 10 else y + 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2
                )

                if collecting:
                    face_crop = frame[y:y + h, x:x + w]
                    if face_crop is not None and face_crop.size > 0 and attempts % 2 == 0:
                        face_images.append(cv2.resize(face_crop, (100, 100)))
                        detection_confidences.append(float(conf))

            attempts += 1 if collecting else 0
            status_text = f'Captured {len(face_images)}/{target_samples} frames' if collecting else 'Press SPACE to start capture'
            cv2.putText(frame, display_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            cv2.putText(frame, status_text, (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            cv2.putText(frame, 'Q or ESC to cancel', (10, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            cv2.imshow('Face Verification', frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord(' ') and not collecting:
                collecting = True
                attempts = 0
            elif key == ord('q') or key == 27:
                face_images = []
                detection_confidences = []
                break

            if collecting and len(face_images) >= target_samples:
                break
            if collecting and attempts >= max_attempts:
                break

        cap.release()
        cv2.destroyAllWindows()

        if not face_images:
            return None

        return {
            'face_images': face_images,
            'confidence': max(detection_confidences) if detection_confidences else 0.0,
            'detection_confidences': detection_confidences,
            'valid_frame_count': len(face_images)
        }

    def get_dataset_for_training(self):
        """Load only enrolled user face images from the dataset for legacy diagnostics."""
        images = []
        labels = []
        dataset_folder = Config.FACE_DATASET_FOLDER
        if not os.path.exists(dataset_folder):
            return np.array([]), np.array([])

        from models.database import Database

        db = Database()
        valid_user_ids = {str(user['id']) for user in db.get_all_users()}
        user_folders = [
            folder_name for folder_name in os.listdir(dataset_folder)
            if os.path.isdir(os.path.join(dataset_folder, folder_name))
        ]

        for folder_name in user_folders:
            if not folder_name.startswith('user_'):
                continue
            user_id = folder_name.split('user_', 1)[1]
            if not user_id.isdigit() or user_id not in valid_user_ids:
                continue

            user_folder = os.path.join(dataset_folder, folder_name)
            for img_file in os.listdir(user_folder):
                if img_file.endswith(('.jpg', '.jpeg', '.png')):
                    img_path = os.path.join(user_folder, img_file)
                    img_color = cv2.imread(img_path)
                    if img_color is None:
                        continue
                    img = cv2.cvtColor(img_color, cv2.COLOR_BGR2GRAY)
                    img = cv2.resize(img, (100, 100))
                    images.append(img)
                    labels.append(folder_name)

        return np.array(images), np.array(labels)
