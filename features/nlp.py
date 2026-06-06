"""
NLP feature engineering.

Computes text similarity between commander and card oracle text.
MVP uses TF-IDF; Phase 2 adds sentence transformers.
"""

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def tfidf_similarity(commander_text: str, card_texts: list) -> list:
    """
    TF-IDF cosine similarity between commander_text and each card text.

    Each card is scored in an isolated [query, card] corpus so scores do not
    change when more candidates are added to the batch (required for stable ranking).
    """
    if not card_texts:
        return []
    if not (commander_text or "").strip():
        return [0.0] * len(card_texts)
    if len(card_texts) == 1:
        corpus = [commander_text, card_texts[0]]
        vectorizer = TfidfVectorizer(stop_words="english")
        matrix = vectorizer.fit_transform(corpus)
        return cosine_similarity(matrix[0:1], matrix[1:])[0].tolist()

    return [
        tfidf_similarity(commander_text, [text])[0]
        for text in card_texts
    ]


def embedding_similarity(commander_text: str, card_texts: list) -> list:
    """Compute sentence transformer embedding similarity (Phase 2)."""
    pass
