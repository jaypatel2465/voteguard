import shutil
from pathlib import Path

import numpy as np

from modules.face_capture import FaceCapture


class DummyDetector:
    def detect_best_face(self, frame):
        return None, 0.0


def test_get_dataset_for_training_ignores_orphan_folders(monkeypatch):
    temp_root = Path('tests/.tmp_face_dataset')
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True)

    monkeypatch.setattr(FaceCapture, '__init__', lambda self: setattr(self, 'detector', DummyDetector()))
    monkeypatch.setattr('config.Config.FACE_DATASET_FOLDER', str(temp_root.resolve()))

    valid_folder = temp_root / 'user_7'
    valid_folder.mkdir()
    orphan_folder = temp_root / '123456789123'
    orphan_folder.mkdir()

    import cv2

    valid_image = np.full((100, 100, 3), 255, dtype=np.uint8)
    orphan_image = np.full((100, 100, 3), 127, dtype=np.uint8)
    cv2.imwrite(str(valid_folder / 'face_0.jpg'), valid_image)
    cv2.imwrite(str(orphan_folder / 'face_0.jpg'), orphan_image)

    import models.database as database_module

    monkeypatch.setattr(database_module.Database, '__init__', lambda self: None)
    monkeypatch.setattr(database_module.Database, 'get_all_users', lambda self: [{'id': 7}])

    capture = FaceCapture()
    images, labels = capture.get_dataset_for_training()

    assert images.shape[0] == 1
    assert labels.tolist() == ['user_7']

    shutil.rmtree(temp_root)
