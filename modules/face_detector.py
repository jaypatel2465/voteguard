"""
Face Detector Module using OpenCV DNN SSD
"""
import cv2
import os
from config import Config

class FaceDetector:
    def __init__(self):
        """Initialize face detector"""
        self.confidence_threshold = Config.FACE_DETECT_CONFIDENCE
        self.min_size = Config.FACE_MIN_SIZE
        self.net = self._load_net()

    def _load_net(self):
        """Load DNN face detector model"""
        proto = Config.FACE_DETECTOR_PROTO
        model = Config.FACE_DETECTOR_MODEL

        if not os.path.exists(proto) or not os.path.exists(model):
            raise FileNotFoundError(
                "Face detector model files not found. "
                "Place deploy.prototxt and res10_300x300_ssd_iter_140000.caffemodel "
                "under models/face_detector/."
            )
        return cv2.dnn.readNetFromCaffe(proto, model)

    def detect_best_face(self, frame):
        """
        Detect the best face in a frame.
        Returns: (bbox, confidence) or (None, 0.0)
        bbox: (x, y, w, h)
        """
        if frame is None:
            return None, 0.0

        (h, w) = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame, (300, 300)),
            1.0,
            (300, 300),
            (104.0, 177.0, 123.0)
        )
        self.net.setInput(blob)
        detections = self.net.forward()

        best_conf = 0.0
        best_box = None

        for i in range(detections.shape[2]):
            confidence = detections[0, 0, i, 2]
            if confidence < self.confidence_threshold:
                continue

            box = detections[0, 0, i, 3:7] * [w, h, w, h]
            (start_x, start_y, end_x, end_y) = box.astype("int")

            x = max(0, start_x)
            y = max(0, start_y)
            end_x = min(w - 1, end_x)
            end_y = min(h - 1, end_y)

            face_w = end_x - x
            face_h = end_y - y

            if face_w < self.min_size or face_h < self.min_size:
                continue

            if confidence > best_conf:
                best_conf = float(confidence)
                best_box = (x, y, face_w, face_h)

        return best_box, best_conf
