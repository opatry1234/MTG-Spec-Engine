"""TF-IDF similarity stability across batch sizes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from features.nlp import tfidf_similarity


def test_tfidf_stable_when_batch_grows():
    query = "zombie discard draw whenever you attack"
    target = "Pay 1 life, Sacrifice a creature: Draw a card."
    filler = [
        "Flying, vigilance",
        "Destroy target artifact or enchantment.",
        "Create a 2/2 black Zombie creature token.",
    ] * 50

    single = tfidf_similarity(query, [target])[0]
    large_batch = tfidf_similarity(query, [target] + filler)[0]

    assert abs(single - large_batch) < 1e-6
