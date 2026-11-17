"""Shared protocol bounds, independent of the network/editor dependencies."""
MIN_STORIES = 2
MAX_STORIES = 20
MAX_SPOKEN_CHARS = 2700
MAX_TURNS = 100
SPEAKERS = frozenset(('Бендер', 'Киберсенсей'))


def number(value: object) -> bool:
    # Batch IDs loaded by PyMongo may be bson.int64.Int64 (an int subclass).
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 999999999999999
