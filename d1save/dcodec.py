"""Diablo 1's save-data codec: a custom SHA1-derived stream cipher used to
encrypt the *contents* of the `hero` and `game` MPQ member files.

This is a separate layer from the MPQ generic table cipher in mpq.py.
Ported from devilutionX's portable reimplementation of Source/codec.cpp
and Source/sha.cpp (which itself replaced the original's dependency on
MSVC's srand(0x7058)-seeded rand() with baked-in constants, so this stays
deterministic without reproducing a specific C runtime's PRNG).

The round function's word-schedule expansion is deliberately NOT real
SHA1 (XOR of w[i-16]^w[i-14]^w[i-8]^w[i-3] with no left-rotate, vs. real
SHA1's rotate-by-1) -- this is original to Diablo's codec, not a bug to
"fix".
"""

MASK32 = 0xFFFFFFFF
BLOCK_SIZE = 16               # words per block
BLOCK_SIZE_BYTES = BLOCK_SIZE * 4
SHA1_HASH_SIZE = 5            # words in the digest

PASSWORD_SINGLE = "xrgyrkj1"  # retail, non-spawn single-player
PASSWORD_MULTI = "szqnlsk1"   # retail, non-spawn multiplayer (closed b.net uses a different scheme entirely)

_INIT_STATE = (0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0)

# Replaces the original's srand(0x7058)-derived rand() output block.
_BASE_KEY = (
    2908958655, 4146550480, 658981742, 1113311088, 3927878744, 679301322, 1760465731, 3305370375,
    2269115995, 3928541685, 580724401, 2607446661, 2233092279, 2416822349, 4106933702, 3046442503,
)


def _circ_shift(word: int, bits: int) -> int:
    word &= MASK32
    if word & 0x80000000:
        return ((0xFFFFFFFF << bits) | (word >> (32 - bits))) & MASK32
    return ((word << bits) | (word >> (32 - bits))) & MASK32


def _process_block(state: list, buffer_words) -> None:
    w = list(buffer_words) + [0] * 64
    for i in range(16, 80):
        w[i] = (w[i - 16] ^ w[i - 14] ^ w[i - 8] ^ w[i - 3]) & MASK32

    a, b, c, d, e = state

    for i in range(0, 20):
        temp = (_circ_shift(a, 5) + ((b & c) | ((~b) & d)) + e + w[i] + 0x5A827999) & MASK32
        e, d, c, b, a = d, c, _circ_shift(b, 30), a, temp
    for i in range(20, 40):
        temp = (_circ_shift(a, 5) + (b ^ c ^ d) + e + w[i] + 0x6ED9EBA1) & MASK32
        e, d, c, b, a = d, c, _circ_shift(b, 30), a, temp
    for i in range(40, 60):
        temp = (_circ_shift(a, 5) + ((b & c) | (b & d) | (c & d)) + e + w[i] + 0x8F1BBCDC) & MASK32
        e, d, c, b, a = d, c, _circ_shift(b, 30), a, temp
    for i in range(60, 80):
        temp = (_circ_shift(a, 5) + (b ^ c ^ d) + e + w[i] + 0xCA62C1D6) & MASK32
        e, d, c, b, a = d, c, _circ_shift(b, 30), a, temp

    state[0] = (state[0] + a) & MASK32
    state[1] = (state[1] + b) & MASK32
    state[2] = (state[2] + c) & MASK32
    state[3] = (state[3] + d) & MASK32
    state[4] = (state[4] + e) & MASK32


def _load_le32_words(data: bytes):
    return [int.from_bytes(data[i:i + 4], "little") for i in range(0, len(data), 4)]


def _dump_le32_words(words) -> bytes:
    out = bytearray()
    for w in words:
        out += (w & MASK32).to_bytes(4, "little")
    return bytes(out)


def _password_words(password: str):
    pw_bytes = password.encode("ascii") + b"\x00"
    j = 0
    words = []
    for _ in range(BLOCK_SIZE):
        if pw_bytes[j] == 0:
            j = 0
        words.append(int.from_bytes(pw_bytes[j:j + 4], "little"))
        j += 4
    return words


def _init_key(password: str) -> list:
    state = list(_INIT_STATE)
    _process_block(state, _password_words(password))
    digest = state

    key = list(_BASE_KEY)
    for i in range(BLOCK_SIZE):
        key[i] ^= digest[(i + 3) % SHA1_HASH_SIZE]

    state2 = list(_INIT_STATE)
    _process_block(state2, key)
    return state2


def decode(data: bytes, password: str) -> bytes:
    """Decrypt (and verify the trailing checksum of) codec-encoded data,
    such as a `hero` or `game` MPQ member's raw bytes."""
    state = _init_key(password)
    if len(data) <= 8:
        return b""
    body_len = len(data) - 8
    if body_len % BLOCK_SIZE_BYTES != 0:
        raise ValueError("ciphertext length is not a multiple of the block size")

    out = bytearray()
    pos = 0
    while pos < body_len:
        words = _load_le32_words(data[pos:pos + BLOCK_SIZE_BYTES])
        digest = list(state)
        words = [(words[i] ^ digest[i % SHA1_HASH_SIZE]) & MASK32 for i in range(BLOCK_SIZE)]
        _process_block(state, words)
        out += _dump_le32_words(words)
        pos += BLOCK_SIZE_BYTES

    sig = data[pos:pos + 8]
    checksum = int.from_bytes(sig[0:4], "little")
    error = sig[4]
    last_chunk_size = sig[5]
    if error > 0:
        raise ValueError("codec signature marks this data as errored")
    if checksum != state[0]:
        raise ValueError("checksum mismatch -- wrong password or corrupt data")

    real_len = body_len + last_chunk_size - BLOCK_SIZE_BYTES
    return bytes(out[:real_len])


def encoded_length(plaintext_len: int) -> int:
    n = plaintext_len
    if n % BLOCK_SIZE_BYTES != 0:
        n += BLOCK_SIZE_BYTES - (n % BLOCK_SIZE_BYTES)
    return n + 8


def encode(plaintext: bytes, password: str) -> bytes:
    state = _init_key(password)
    out = bytearray()

    pos = 0
    size = len(plaintext)
    last_chunk = 0
    while size != 0:
        chunk = min(size, BLOCK_SIZE_BYTES)
        buf = bytearray(plaintext[pos:pos + chunk])
        if chunk < BLOCK_SIZE_BYTES:
            buf += b"\x00" * (BLOCK_SIZE_BYTES - chunk)
        words = _load_le32_words(bytes(buf))
        digest = list(state)
        _process_block(state, words)
        words = [(words[i] ^ digest[i % SHA1_HASH_SIZE]) & MASK32 for i in range(BLOCK_SIZE)]
        out += _dump_le32_words(words)
        pos += chunk
        last_chunk = chunk
        size -= chunk

    checksum = state[0]
    out += checksum.to_bytes(4, "little")
    out += bytes([0, last_chunk & 0xFF, 0, 0])

    assert len(out) == encoded_length(len(plaintext))
    return bytes(out)
