import numpy as np

GOLDEN_RATIO_64 = np.uint64(0x61C8864680B583EB)
CONFIG_SEED_BUCKETS = 4
PTR_SIZE = 8

def fio_fill_random_buf(buf: bytearray, length: int, seed: np.uint64):
    primes = [1, 2, 3, 5, 7, 11, 13, 17,
              19, 23, 29, 31, 37, 41, 43, 47]

    assert len(buf) >= length, "Buffer is smaller than length"

    total_words = length // PTR_SIZE
    rest_words = (total_words // CONFIG_SEED_BUCKETS) * CONFIG_SEED_BUCKETS
    rest_bytes = length - (rest_words * PTR_SIZE)

    # Prepare initial seeds per bucket
    s = [np.uint64(seed * primes[i]) for i in range(CONFIG_SEED_BUCKETS)]

    offset = 0
    while offset < rest_words * 8:
        for p in range(CONFIG_SEED_BUCKETS):
            val = int(s[p]).to_bytes(PTR_SIZE, byteorder='little')
            buf[offset:offset + PTR_SIZE] = val
            s[p] = __hash_u64(s[p])
            offset += PTR_SIZE

    # Fill the rest using small filler
    if rest_bytes:
        __fio_fill_random_buf_small(buf[offset:], rest_bytes, s[0])

def __fio_fill_random_buf_small(buf: bytearray, length: int, seed: np.uint64):
    # Interpret buffer as uint64 array (up to the last full 8-byte element)
    count = length // PTR_SIZE
    rest = length % PTR_SIZE

    for i in range(count):
        value = int(seed).to_bytes(8, byteorder='little')
        buf[i * PTR_SIZE: i * PTR_SIZE + PTR_SIZE] = value
        seed = __hash_u64(seed)

    if rest:
        buf[count * PTR_SIZE: count * PTR_SIZE + rest] = int(seed).to_bytes(8, byteorder='little')[:rest]

def __hash_u64(val: np.uint64) -> np.uint64:
    return np.uint64(val * GOLDEN_RATIO_64)
