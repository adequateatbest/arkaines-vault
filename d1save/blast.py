"""Pure-Python PKWARE Data Compression Library ("implode"/"explode") decoder.

Ported directly from zlib's contrib/blast (blast.c, Mark Adler) -- the
reference decompressor for the format MPQ calls MPQ_FILE_IMPLODE. This is a
from-scratch Python port of the algorithm (Huffman table construction and
the bit-reversed canonical decode), not a wrapper around a compiled binary:
there is no C compiler on this machine, and a pure-Python implementation is
also simply more portable for a tool other people will run.

One deliberate simplification vs. blast.c: the original keeps a 4096-byte
*circular* output window (MAXWIN) to bound memory use during streaming.
Here we use a plain growing bytearray instead. This is safe because the
format guarantees every back-reference distance is <= 4096, and history is
never discarded in this implementation -- so indexing "dist bytes before
the current end" into a growing buffer is always valid and byte-identical
to what the circular window would produce. We do not need blast.c's
streaming infun/outfun callbacks either, since MPQ sectors are small and
fully buffered already.
"""

MAXBITS = 13

_LITLEN = bytes([
    11, 124, 8, 7, 28, 7, 188, 13, 76, 4, 10, 8, 12, 10, 12, 10, 8, 23, 8,
    9, 7, 6, 7, 8, 7, 6, 55, 8, 23, 24, 12, 11, 7, 9, 11, 12, 6, 7, 22, 5,
    7, 24, 6, 11, 9, 6, 7, 22, 7, 11, 38, 7, 9, 8, 25, 11, 8, 11, 9, 12,
    8, 12, 5, 38, 5, 38, 5, 11, 7, 5, 6, 21, 6, 10, 53, 8, 7, 24, 10, 27,
    44, 253, 253, 253, 252, 252, 252, 13, 12, 45, 12, 45, 12, 61, 12, 45,
    44, 173,
])
_LENLEN = bytes([2, 35, 36, 53, 38, 23])
_DISTLEN = bytes([2, 20, 53, 230, 247, 151, 248])

_LENBASE = [3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264]
_LENEXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8]


class _Huffman:
    __slots__ = ("count", "symbol")

    def __init__(self, count, symbol):
        self.count = count
        self.symbol = symbol


def _construct(rep: bytes) -> _Huffman:
    """Port of blast.c's construct(): expand compact repeat-count-coded
    lengths into a code-length list, then build canonical Huffman tables
    (count-per-length, symbols-sorted-by-length) from it."""
    length = []
    for b in rep:
        reps = (b >> 4) + 1
        code_len = b & 15
        length.extend([code_len] * reps)
    n = len(length)

    count = [0] * (MAXBITS + 1)
    for code_len in length:
        count[code_len] += 1

    symbol = [0] * n
    if count[0] == n:
        return _Huffman(count, symbol)  # empty code -- never hit for our fixed tables

    offs = [0] * (MAXBITS + 1)
    for l in range(1, MAXBITS):
        offs[l + 1] = offs[l] + count[l]

    for sym, code_len in enumerate(length):
        if code_len != 0:
            symbol[offs[code_len]] = sym
            offs[code_len] += 1

    return _Huffman(count, symbol)


_LITCODE = _construct(_LITLEN)
_LENCODE = _construct(_LENLEN)
_DISTCODE = _construct(_DISTLEN)


class _BitReader:
    __slots__ = ("data", "pos", "bitbuf", "bitcnt")

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.bitbuf = 0
        self.bitcnt = 0

    def bits(self, need: int) -> int:
        val = self.bitbuf
        cnt = self.bitcnt
        while cnt < need:
            if self.pos >= len(self.data):
                raise EOFError("blast: out of input")
            val |= self.data[self.pos] << cnt
            self.pos += 1
            cnt += 8
        self.bitbuf = val >> need
        self.bitcnt = cnt - need
        return val & ((1 << need) - 1)

    def decode(self, h: _Huffman) -> int:
        """Port of blast.c's decode(): bit-reversed canonical Huffman
        decode, operating directly on the raw bit-buffer state the same
        way the original does (rather than through bits())."""
        bitbuf = self.bitbuf
        left = self.bitcnt
        code = 0
        first = 0
        index = 0
        length_ = 1
        next_idx = 1
        while True:
            while left:
                left -= 1
                code |= (bitbuf & 1) ^ 1
                bitbuf >>= 1
                count = h.count[next_idx]
                next_idx += 1
                if code < first + count:
                    self.bitbuf = bitbuf
                    self.bitcnt = (self.bitcnt - length_) & 7
                    return h.symbol[index + (code - first)]
                index += count
                first += count
                first <<= 1
                code <<= 1
                length_ += 1
            left = (MAXBITS + 1) - length_
            if left == 0:
                raise ValueError("blast: ran out of codes (corrupt stream)")
            if self.pos >= len(self.data):
                raise EOFError("blast: out of input (huffman decode)")
            bitbuf = self.data[self.pos]
            self.pos += 1
            if left > 8:
                left = 8


def explode(data: bytes) -> bytes:
    """Decompress one PKWARE-imploded block (e.g. one MPQ sector's raw
    bytes, or a single-unit file's whole body). `data` starts with the
    2-byte header (literal-coding flag, dictionary-size code) followed by
    the coded stream; the stream is self-terminating (end code), so no
    expected output length is needed."""
    br = _BitReader(data)

    lit = br.bits(8)
    if lit > 1:
        raise ValueError("blast: invalid literal-coding flag in header")
    dict_bits = br.bits(8)
    if dict_bits < 4 or dict_bits > 6:
        raise ValueError("blast: invalid dictionary-size code in header")

    out = bytearray()
    while True:
        if br.bits(1):
            symbol = br.decode(_LENCODE)
            length_ = _LENBASE[symbol] + br.bits(_LENEXTRA[symbol])
            if length_ == 519:
                break  # end code

            dist_extra_bits = 2 if length_ == 2 else dict_bits
            dist = (br.decode(_DISTCODE) << dist_extra_bits) + br.bits(dist_extra_bits) + 1

            if dist > len(out):
                raise ValueError("blast: distance too far back (corrupt stream)")
            start = len(out) - dist
            for i in range(length_):
                out.append(out[start + i])
        else:
            symbol = br.decode(_LITCODE) if lit else br.bits(8)
            out.append(symbol)

    return bytes(out)
