"""Minimal MPQ (MoPaQ v1) reader/writer for Diablo 1 save files.

Two things this deliberately does NOT do, both load-bearing:

1. It does not use mpyq's own `read_file()` decompression path as-is.
   mpyq's `decompress()` only recognizes compression type bytes for
   zlib/bzip2/"none" and never checks the MPQ_FILE_IMPLODE block flag
   (0x100) at all -- so for a PKWARE-imploded file (which is what
   Diablo 1 saves use) it silently hands back still-compressed bytes
   with no error. `read_file()` here checks MPQ_FILE_IMPLODE explicitly
   and routes those bytes through `blast.explode()`.

2. It never does a generic open/modify/recompact rewrite of the archive
   (e.g. via a full StormLib-style rebuild). That shrinks the block
   table to just the files in use, which is spec-legal MPQ but crashes
   the real Diablo.exe on load -- verified empirically. `patch_files()`
   instead does a minimal surgical patch: reuse the original header,
   hash table, and (parsed) block table as-is; only the target
   entries' (offset, archived_size, size, flags) change, pointing at
   freshly appended data; the hash table is untouched; nothing else in
   the file moves.
"""
import struct
import mpyq

from . import blast

MPQ_FILE_IMPLODE = 0x00000100
MPQ_FILE_COMPRESS = 0x00000200
MPQ_FILE_ENCRYPTED = 0x00010000
MPQ_FILE_SINGLE_UNIT = 0x01000000
MPQ_FILE_SECTOR_CRC = 0x04000000
MPQ_FILE_EXISTS = 0x80000000

HEADER_FMT = '<4sIIHHIIII'
HEADER_SIZE = struct.calcsize(HEADER_FMT)
assert HEADER_SIZE == 32

ARCHIVE_SIZE_FIELD_OFFSET = 8  # byte offset of the 'archive_size' header field

encryption_table = mpyq.MPQArchive.encryption_table


def mpq_hash(name: str, hash_type: str) -> int:
    """MPQ generic name hash (used for hash-table lookups and to derive the
    table-encryption keys). Not Diablo's own save-data codec (see dcodec.py)
    -- this is the standard housekeeping-table cipher MPQ itself uses."""
    hash_types = {'TABLE_OFFSET': 0, 'HASH_A': 1, 'HASH_B': 2, 'TABLE': 3}
    seed1 = 0x7FED7FED
    seed2 = 0xEEEEEEEE
    for ch in name.upper():
        value = encryption_table[(hash_types[hash_type] << 8) + ord(ch)]
        seed1 = (value ^ (seed1 + seed2)) & 0xFFFFFFFF
        seed2 = (ord(ch) + seed1 + seed2 + (seed2 << 5) + 3) & 0xFFFFFFFF
    return seed1


def mpq_decrypt(data: bytes, key: int) -> bytes:
    seed1 = key
    seed2 = 0xEEEEEEEE
    out = bytearray(len(data))
    for i in range(len(data) // 4):
        seed2 = (seed2 + encryption_table[0x400 + (seed1 & 0xFF)]) & 0xFFFFFFFF
        value = struct.unpack_from('<I', data, i * 4)[0]
        value = (value ^ (seed1 + seed2)) & 0xFFFFFFFF
        seed1 = (((~seed1 << 0x15) & 0xFFFFFFFF) + 0x11111111) | (seed1 >> 0x0B)
        seed1 &= 0xFFFFFFFF
        seed2 = (value + seed2 + (seed2 << 5) + 3) & 0xFFFFFFFF
        struct.pack_into('<I', out, i * 4, value)
    return bytes(out)


def mpq_encrypt(data: bytes, key: int) -> bytes:
    """Inverse of mpq_decrypt(). The keystream state (seed2) evolves from
    the *plaintext* dword in both directions, so this is decrypt's loop
    with input/output swapped, not a different algorithm."""
    seed1 = key
    seed2 = 0xEEEEEEEE
    out = bytearray(len(data))
    for i in range(len(data) // 4):
        seed2 = (seed2 + encryption_table[0x400 + (seed1 & 0xFF)]) & 0xFFFFFFFF
        plain = struct.unpack_from('<I', data, i * 4)[0]
        cipher = (plain ^ (seed1 + seed2)) & 0xFFFFFFFF
        seed1 = (((~seed1 << 0x15) & 0xFFFFFFFF) + 0x11111111) | (seed1 >> 0x0B)
        seed1 &= 0xFFFFFFFF
        seed2 = (plain + seed2 + (seed2 << 5) + 3) & 0xFFFFFFFF
        struct.pack_into('<I', out, i * 4, cipher)
    return bytes(out)


def open_archive(path) -> mpyq.MPQArchive:
    """Open a Diablo 1 save as an MPQ archive. There is no listfile in
    these saves -- files are located purely by name hash -- so listfile
    reading must stay off or mpyq crashes on init."""
    return mpyq.MPQArchive(path, listfile=False)


def _decompress_chunk(chunk: bytes, flags: int) -> bytes:
    if flags & MPQ_FILE_IMPLODE:
        return blast.explode(chunk)
    if flags & MPQ_FILE_COMPRESS:
        method = chunk[0]
        body = chunk[1:]
        if method == 0:
            return body
        if method & 0x02:
            import zlib
            return zlib.decompress(body, 15)
        if method & 0x10:
            import bz2
            return bz2.decompress(body)
        if method & 0x08:
            return blast.explode(body)
        raise ValueError(f"unsupported MPQ sector compression method byte 0x{method:02x}")
    return chunk


def read_file(archive, filename: str) -> bytes:
    """Read and fully decompress a file from an open MPQArchive (see
    open_archive()), correctly handling MPQ_FILE_IMPLODE."""
    hash_entry = archive.get_hash_table_entry(filename)
    if hash_entry is None:
        raise KeyError(f"{filename!r} not found in archive")
    block_entry = archive.block_table[hash_entry.block_table_index]

    if not (block_entry.flags & MPQ_FILE_EXISTS):
        raise KeyError(f"{filename!r} hash entry present but marked not-exists")
    if block_entry.archived_size == 0:
        return b""

    archive.file.seek(block_entry.offset + archive.header['offset'])
    raw = archive.file.read(block_entry.archived_size)

    if block_entry.flags & MPQ_FILE_ENCRYPTED:
        raise NotImplementedError("encrypted MPQ file bodies aren't needed for D1 saves")

    compress_flags = block_entry.flags & (MPQ_FILE_IMPLODE | MPQ_FILE_COMPRESS)

    if block_entry.flags & MPQ_FILE_SINGLE_UNIT:
        if compress_flags and block_entry.size > block_entry.archived_size:
            return _decompress_chunk(raw, block_entry.flags)
        return raw[:block_entry.size]

    # Multi-sector file: a (numSectors+1)-entry uint32 position table,
    # each sector independently compressed only if that made it smaller.
    sector_size = 512 << archive.header['sector_size_shift']
    num_sectors = (block_entry.size + sector_size - 1) // sector_size if block_entry.size else 1
    num_positions = num_sectors + 1
    has_crc = bool(block_entry.flags & MPQ_FILE_SECTOR_CRC)
    if has_crc:
        num_positions += 1
    positions = struct.unpack('<%dI' % num_positions, raw[:4 * num_positions])

    out = bytearray()
    remaining = block_entry.size
    last_data_index = num_positions - (2 if has_crc else 1)
    for i in range(last_data_index):
        sector = raw[positions[i]:positions[i + 1]]
        this_sector_size = min(sector_size, remaining)
        if compress_flags and len(sector) < this_sector_size:
            sector = _decompress_chunk(sector, block_entry.flags)
        out += sector
        remaining -= this_sector_size
    return bytes(out)


def patch_files(in_path, out_path, updates: dict):
    """Minimal surgical patch: write each `updates[filename]` into the
    archive as MPQ_FILE_SINGLE_UNIT, uncompressed (no implode encoder is
    implemented -- none is needed since the engine reads uncompressed
    single-unit files fine). Only the affected block-table entries
    change, at their original table offset; the hash table, and every
    other file's bytes, are untouched. Returns {filename: (block_idx,
    new_offset, size)}.
    """
    with open(in_path, 'rb') as f:
        data = bytearray(f.read())

    magic, header_size, archive_size, format_version, sector_size_shift, \
        hash_table_offset, block_table_offset, hash_table_entries, block_table_entries = \
        struct.unpack_from(HEADER_FMT, data, 0)
    assert magic == b'MPQ\x1a', "not a plain (non-userdata) MPQ v1 archive"
    assert format_version == 0, "only plain MPQ v1 headers are supported by this patcher"

    archive = mpyq.MPQArchive(in_path, listfile=False)
    block_entries = list(archive.block_table)

    # Assign append offsets in a stable order (dict iteration order ==
    # insertion order in Python 3.7+) before touching the block table.
    offsets = {}
    append_offset = len(data)
    for filename, new_data in updates.items():
        offsets[filename] = append_offset
        append_offset += len(new_data)

    result = {}
    for filename, new_data in updates.items():
        hash_entry = archive.get_hash_table_entry(filename)
        assert hash_entry is not None, f"{filename!r} not found in archive"
        block_idx = hash_entry.block_table_index
        block_entries[block_idx] = mpyq.MPQBlockTableEntry(
            offset=offsets[filename],
            archived_size=len(new_data),
            size=len(new_data),
            flags=MPQ_FILE_EXISTS | MPQ_FILE_SINGLE_UNIT,
        )
        result[filename] = (block_idx, offsets[filename], len(new_data))

    plaintext = b''.join(
        struct.pack('<4I', e.offset, e.archived_size, e.size, e.flags)
        for e in block_entries
    )
    assert len(plaintext) == block_table_entries * 16

    block_key = mpq_hash('(block table)', 'TABLE')
    new_block_table_bytes = mpq_encrypt(plaintext, block_key)
    assert mpq_decrypt(new_block_table_bytes, block_key) == plaintext, \
        "block table crypto round-trip failed"

    data[block_table_offset:block_table_offset + len(new_block_table_bytes)] = new_block_table_bytes

    for filename, new_data in updates.items():
        data += new_data

    struct.pack_into('<I', data, ARCHIVE_SIZE_FIELD_OFFSET, len(data))

    with open(out_path, 'wb') as f:
        f.write(data)

    return result
