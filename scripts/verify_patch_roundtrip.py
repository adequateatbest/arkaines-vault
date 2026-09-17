"""Verify d1save.mpq.patch_files() only touches what it should:
round-trip hero+game through decode/encode unchanged, patch them back in,
then assert the patched archive is byte-identical to the original except
for (a) the block table region and (b) the newly appended bytes -- i.e.
everything else (header, hash table, and every existing file's raw
bytes, whatever their names) is untouched.

Usage: python scripts/verify_patch_roundtrip.py path/to/save.sv
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from d1save import mpq, dcodec


def main():
    save_path = sys.argv[1] if len(sys.argv) > 1 else "single_0.sv"
    out_path = str(Path(save_path).with_suffix(".patched_test.sv"))

    arc = mpq.open_archive(save_path)
    original_bytes = Path(save_path).read_bytes()

    updates = {}
    for name in ("hero", "game"):
        hash_entry = arc.get_hash_table_entry(name)
        if hash_entry is None:
            print(f"  ({name!r} not present in this archive, skipping)")
            continue
        raw = mpq.read_file(arc, name)
        plain = dcodec.decode(raw, dcodec.PASSWORD_SINGLE)
        reencoded = dcodec.encode(plain, dcodec.PASSWORD_SINGLE)
        redecoded = dcodec.decode(reencoded, dcodec.PASSWORD_SINGLE)
        assert redecoded == plain, f"{name}: decode(encode(x)) != x"
        updates[name] = reencoded
        print(f"  {name}: {len(raw)} raw -> {len(plain)} plaintext -> {len(reencoded)} re-encoded")

    result = mpq.patch_files(save_path, out_path, updates)
    for name, (block_idx, offset, size) in result.items():
        print(f"  patched {name}: block_idx={block_idx} offset={offset} size={size}")

    patched_bytes = Path(out_path).read_bytes()

    header_fmt = mpq.HEADER_FMT
    _, _, archive_size, _, _, hash_table_offset, block_table_offset, hash_table_entries, block_table_entries = \
        struct.unpack_from(header_fmt, original_bytes, 0)

    block_table_size = block_table_entries * 16

    # 1) everything before the block table is untouched, EXCEPT the 4-byte
    # archive_size header field (offset 8), which is expected to change.
    asf = mpq.ARCHIVE_SIZE_FIELD_OFFSET
    assert original_bytes[:asf] == patched_bytes[:asf], "header changed before archive_size field!"
    assert original_bytes[asf + 4:block_table_offset] == patched_bytes[asf + 4:block_table_offset], \
        "header/hash-table region changed (outside archive_size field)!"
    print("  OK: header + hash table byte-identical (aside from the archive_size field)")

    # 2) block table: decrypt both, confirm only the patched files' entries differ
    key = mpq.mpq_hash('(block table)', 'TABLE')
    orig_bt = mpq.mpq_decrypt(
        original_bytes[block_table_offset:block_table_offset + block_table_size], key)
    new_bt = mpq.mpq_decrypt(
        patched_bytes[block_table_offset:block_table_offset + block_table_size], key)

    changed_indices = {result[name][0] for name in updates}
    for i in range(block_table_entries):
        entry_orig = orig_bt[i * 16:i * 16 + 16]
        entry_new = new_bt[i * 16:i * 16 + 16]
        if i in changed_indices:
            assert entry_orig != entry_new, f"block entry {i} should have changed but didn't"
        else:
            assert entry_orig == entry_new, f"block entry {i} changed but shouldn't have"
    print(f"  OK: block table -- only {sorted(changed_indices)} changed, "
          f"other {block_table_entries - len(changed_indices)} entries untouched")

    # 3) everything from the end of the block table to the end of the
    # ORIGINAL file (i.e. every existing file's bytes, whatever their
    # names) is untouched
    tail_start = block_table_offset + block_table_size
    original_tail = original_bytes[tail_start:len(original_bytes)]
    patched_tail_same_region = patched_bytes[tail_start:tail_start + len(original_tail)]
    assert original_tail == patched_tail_same_region, "existing file data changed!"
    print(f"  OK: {len(original_tail)} bytes of existing file data byte-identical")

    # 4) appended bytes match what we asked to write, and archive_size header is correct
    expected_new_len = len(original_bytes) + sum(len(d) for d in updates.values())
    assert len(patched_bytes) == expected_new_len, "unexpected total length"
    patched_header_size = struct.unpack_from('<I', patched_bytes, mpq.ARCHIVE_SIZE_FIELD_OFFSET)[0]
    assert patched_header_size == len(patched_bytes), "archive_size header field wrong"
    pos = len(original_bytes)
    for name, data in updates.items():
        assert patched_bytes[pos:pos + len(data)] == data, f"{name}: appended bytes mismatch"
        pos += len(data)
    print("  OK: appended bytes match, archive_size header correct")

    print("\nAll checks passed.")
    Path(out_path).unlink()


if __name__ == "__main__":
    main()
