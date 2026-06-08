import numpy as np

from modules.face_embeddings import FaceEmbeddingHandler


class DummyEmbedder:
    def setInput(self, blob):
        pass

    def forward(self):
        return np.zeros((1, 128), dtype='float32')


def _handler(monkeypatch):
    monkeypatch.setattr(FaceEmbeddingHandler, '_load_embedder', lambda self: DummyEmbedder())
    return FaceEmbeddingHandler()


def test_identify_user_requires_strong_unambiguous_match(monkeypatch):
    handler = _handler(monkeypatch)
    live_embeddings = [np.array([1.0, 0.0, 0.0], dtype='float32') for _ in range(8)]
    existing = [
        {'user_id': 1, 'embedding_data': np.array([1.0, 0.0, 0.0], dtype='float32')},
        {'user_id': 1, 'embedding_data': np.array([0.98, 0.02, 0.0], dtype='float32')},
        {'user_id': 2, 'embedding_data': np.array([0.0, 1.0, 0.0], dtype='float32')},
    ]

    result = handler.identify_user(live_embeddings, existing)

    assert result['success'] is True
    assert result['winner_user_id'] == 1
    assert result['winner_score'] >= 0.99
    assert result['score_margin'] >= 0.08


def test_identify_user_rejects_weak_match(monkeypatch):
    handler = _handler(monkeypatch)
    live_embeddings = [np.array([1.0, 0.0, 0.0], dtype='float32') for _ in range(8)]
    existing = [
        {'user_id': 1, 'embedding_data': np.array([0.7, 0.7, 0.0], dtype='float32')},
        {'user_id': 2, 'embedding_data': np.array([0.0, 1.0, 0.0], dtype='float32')},
    ]

    result = handler.identify_user(live_embeddings, existing)

    assert result['success'] is False
    assert result['reason'] == 'weak_match'


def test_identify_user_rejects_ambiguous_match(monkeypatch):
    handler = _handler(monkeypatch)
    live_embeddings = [np.array([1.0, 0.0, 0.0], dtype='float32') for _ in range(8)]
    existing = [
        {'user_id': 1, 'embedding_data': np.array([1.0, 0.0, 0.0], dtype='float32')},
        {'user_id': 2, 'embedding_data': np.array([0.99, 0.1, 0.0], dtype='float32')},
    ]

    result = handler.identify_user(live_embeddings, existing)

    assert result['success'] is False
    assert result['reason'] == 'ambiguous_match'
    assert result['runner_up_user_id'] == 2


def test_check_duplicate_uses_grouped_matching(monkeypatch):
    handler = _handler(monkeypatch)
    new_embeddings = [np.array([1.0, 0.0, 0.0], dtype='float32') for _ in range(8)]
    existing = [
        {'user_id': 4, 'embedding_data': np.array([1.0, 0.0, 0.0], dtype='float32')},
        {'user_id': 4, 'embedding_data': np.array([0.99, 0.05, 0.0], dtype='float32')},
    ]

    duplicate = handler.check_duplicate(new_embeddings, existing)

    assert duplicate['is_duplicate'] is True
    assert duplicate['matched_user_id'] == 4
    assert duplicate['analysis']['winner_score'] >= 0.99
