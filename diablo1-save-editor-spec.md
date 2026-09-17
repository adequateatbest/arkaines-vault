# Diablo 1 Save Editor — Technical Spec

Reverse-engineering notes from a live debugging session against a real
Battle.net-mode `single_0.sv` file (non-Hellfire, "RETL" build). Everything
here was empirically verified against an actual save, not just read from
source. Use this to seed a Claude Code project rather than rediscovering it.

## 0. Scope and interface

**In scope for v1:**
- Item injection/editing (equip slots, backpack, belt) — everything covered
  in §5–§7 below.
- Character stat editing (level, experience, gold, Str/Mag/Dex/Vit,
  HP/Mana base values, etc.) — same two-file (`hero`/`game`) duality applies
  here too; both compact and raw struct layouts expose these fields at the
  offsets noted in §4 and §5.

**Explicitly deferred (not v1):**
- Ground-item placement (needs the per-level `perml*`/`perms*` dungeon-state
  files, not just player state).
- Quest-state editing.
- Multiplayer/closed-Battle.net save support (different password derivation).
- A PKWARE implode encoder (not needed given the single-unit-uncompressed
  write workaround in §1).

**Interface:** GUI. Exact framework/design undecided — pick whatever's
comfortable to iterate on with Claude Code (e.g. a simple desktop app rather
than a web stack, since this reads/writes local files and doesn't need to be
distributed). Suggest deciding this once the read/write core (§8) is working
end-to-end via a throwaway CLI, so the GUI has stable logic to sit on top of
rather than being designed in parallel with format bugs still being found.

Primary source used throughout: the `diasurgical/devilution` decompilation
(https://github.com/diasurgical/devilution) — a faithful decompile of the
original 1.09 binary. Cross-checked against real exported item data and the
save file's own leftover bytes wherever possible.

## 1. Container format: MPQ (MoPaQ), version 1

`single_0.sv` is an old-style (v1) MPQ archive. Key facts that trip up naive
tooling:

- **No listfile.** Open with `listfile=False` if using `mpyq`, or you'll
  crash on init. Files are located purely by name hash.
- **Compression is PKWARE "implode" (DCL), not zlib/bzip2.** Files with block
  flag `0x100` (`MPQ_FILE_IMPLODE`) are compressed with the old PKWARE Data
  Compression Library format — the same as ZIP's "imploding" method.
  `mpyq`'s built-in decompressor does **not** recognize this flag as
  compression, and will silently hand you still-compressed bytes.
- **Decompression:** use zlib's `contrib/blast` reference implementation
  (`blast.c`/`blast.h`, from https://github.com/madler/zlib) — it implements
  exactly this DCL "explode" algorithm. Compile a tiny CLI wrapper around
  `blast()` and shell out to it, or wrap it directly.
- **No implode *encoder* is readily available.** Rather than implementing
  one, store any rewritten file as **`MPQ_FILE_SINGLE_UNIT`, uncompressed**
  (flags = `MPQ_FILE_EXISTS | MPQ_FILE_SINGLE_UNIT`, no compression bit).
  This is valid, spec-legal, and the original engine reads it fine — no
  encoder needed.
- **Multi-sector files** (anything bigger than one sector, default sector
  size `512 << sectorSizeShift`, typically 4096 bytes) are split into
  sectors, each independently imploded, preceded by a `(numSectors+1)`-entry
  `uint32` position table. Decompress sector-by-sector and concatenate.

### Patch strategy — do NOT do a full read/write cycle with a generic MPQ
### library

This was the actual root cause of one full debugging cycle: using StormLib
to open, modify, and let it recompact the archive **shrank the block table**
from its original allocated size down to just the number of files in use.
That's spec-legal MPQ, but it **crashed the real Diablo.exe** on load. The
original engine is far less forgiving than modern reimplementations.

**Safe approach: minimal surgical patch.**
1. Parse the original file's header, hash table, and block table (all
   readable via `mpyq`).
2. Find the target file's hash-table entry → block-table index.
3. Rebuild the **entire block table plaintext** (not just one entry — see
   why below), with only the target entry's `(offset, archived_size, size,
   flags)` changed to point at newly-appended data.
4. Re-encrypt the whole block table with the standard MPQ table cipher (see
   §2) and write it back **at its original offset** — same size, same
   position, nothing else about the file moves.
5. Append the new file's raw bytes at the very end of the file; point the
   modified block-table entry's `offset` there.
6. Patch the 4-byte `archiveSize` header field to the new total length.
7. Leave the hash table completely untouched.

This keeps the file byte-identical to the original except for (a) the one
block-table entry and (b) the appended bytes — verified by diffing every
other file's raw bytes between original and patched archives.

**Why rebuild the whole block table, not just one entry:** the MPQ table
cipher is a stream cipher whose keystream state (`seed2`) evolves based on
the *plaintext* of every preceding 32-bit word. Splicing in a re-encrypted
single entry produces wrong ciphertext for everything after it. Re-encrypt
the full table from a fully-known plaintext (you have all entries from
parsing) and this is a non-issue.

## 2. MPQ table encryption (hash table / block table)

Standard, well-documented MoPaQ generic encryption — NOT Diablo's own game
codec (see §3), this is a separate layer used for the housekeeping tables.

- Crypt table: 0x500 (1280) `uint32` entries, generated by a standard PRNG
  seeding algorithm (`mpyq` already implements this; reuse its
  `encryption_table`).
- Hash key for a name+type: `seed1=0x7FED7FED, seed2=0xEEEEEEEE`, iterate
  uppercased characters, standard MPQ hash function. Table keys are
  `Hash("(hash table)", "TABLE")` / `Hash("(block table)", "TABLE")`.
- Decrypt: for each `uint32` dword, `seed2 += table[0x400+(seed1&0xFF)]`,
  `value = dword ^ (seed1+seed2)`, then update
  `seed1 = ((~seed1<<21)+0x11111111) | (seed1>>11)` (pure function of
  seed1) and `seed2 = value + seed2 + (seed2<<5) + 3` (depends on the
  **decrypted plaintext** value — this is what makes it a stream cipher over
  the plaintext).
- Encrypt is the mirror image: same seed2 update uses the **plaintext**
  input value (matches decrypt's use of the recovered plaintext), so you can
  literally reuse the decrypt loop's math with input/output swapped.

## 3. Diablo's own save-data codec (separate from §2)

This encrypts the *contents* of the `hero` and `game` files specifically
(the compact player summary and the full game-state snapshot). It is a
custom SHA1-based stream cipher, not the MPQ table cipher above.

- Passwords: `xrgyrkj1` for `PASSWORD_SINGLE` (retail, non-spawn),
  `szqnlsk1` for `PASSWORD_MULTI` (build with `#ifdef SPAWN` uses different
  strings — irrelevant for retail).
- Block size: 16 `uint32` words (64 bytes) per block.
- Key setup: build a 16-word block from the password (repeating/wrapping the
  password bytes to fill 64 bytes), run one custom-SHA1 block-compression
  pass over it to get a digest, XOR that digest into a **hardcoded constant
  16-word base key** (this constant replaces what was originally
  `srand(0x7058)`-derived `rand()` output on the original MSVC runtime — see
  `devilutionX`'s `codec.cpp`, which bakes in the resulting constants so it's
  portable/deterministic without needing to replicate MSVC's `rand()`), then
  run another compression pass over that to get the actual initial cipher
  state.
- Stream cipher loop per 64-byte block: take current state's digest, XOR it
  word-by-word into the block, then feed the **resulting plaintext** block
  into the next compression-state update (same block, whether encoding or
  decoding — this keeps encode/decode symmetric).
- Custom SHA1 detail: the round function's word-schedule expansion is
  **not** the real SHA1 spec (uses XOR of `w[i-16]^w[i-14]^w[i-8]^w[i-3]`
  with no left-rotate, unlike real SHA1's rotate-by-1) — this is
  intentional/original to Diablo, don't "fix" it to match real SHA1 or
  everything breaks.
- Trailer: 8-byte `CodecSignature` appended after all blocks — `checksum`
  (first word of final digest, 4 bytes), `error` byte, `lastChunkSize` byte,
  2 unused bytes. Total encoded length is always
  `ceil(plaintextLen / 64) * 64 + 8`.
- Full working Python reimplementation exists (ported from
  `devilutionX/Source/codec.cpp` and `sha.cpp`) — reuse it verbatim rather
  than re-deriving.

## 4. Two independent player-data representations — the critical gotcha

This is the thing that will burn the most time if missed. A Diablo 1
single-player save can contain **both**:

- **`hero`** — a compact, packed (`#pragma pack(push,1)`) summary
  (`PkPlayerStruct` / `PkItemStruct`). Used for the character-select screen
  list. **Not necessarily what's used to resume gameplay.**
- **`game`** — present **only if the character has an in-progress dungeon
  run** (i.e., wasn't saved standing safely in town). Contains a raw,
  natural-compiler-alignment memory dump of the live `PlayerStruct` (and
  monster/missile/object/quest/etc. arrays) exactly as loaded into RAM —
  a completely different, much larger struct layout using full 32-bit
  `ItemStruct` entries (368 bytes each) rather than the compact 19-byte
  `PkItemStruct`.

**If a `game` file exists in the archive, that's what actually gets loaded
into the live character when continuing — edits to `hero` alone will appear
to silently do nothing** (no crash, other stats look right, but inventory
edits never show up). Check for a file named `game` in the archive
(`pfile_archive_contains_game` in source checks exactly this) before
assuming `hero` is sufficient. If present, edit **both** for consistency
(`hero` for the character-select list, `game` for actual gameplay), or just
`game` if you don't care about the select-screen preview.

Both `hero` and `game` are encrypted with the *same* codec (§3) and the same
`PASSWORD_SINGLE`. `game` is typically large enough to span multiple MPQ
sectors (see §1's multi-sector note), `hero` typically fits in one.

### `game` file structure (non-Hellfire / `'RETL'` magic build)

Header before the raw player dump, all fields read **big-endian** (4-byte
values shifted in MSB-first) despite the rest of the blob being native
little-endian raw struct dumps — an intentional quirk, don't "normalize" it:

```
magic            4 bytes  ('RETL' for non-Hellfire retail; 'HELF' for Hellfire; 'SHAR' for spawn/shareware)
setlevel         1 byte   (bool)
setlvlnum        4 bytes
currlevel        4 bytes
leveltype        4 bytes
_ViewX           4 bytes
_ViewY           4 bytes
invflag          1 byte   (bool)
chrflag          1 byte   (bool)
_nummonsters     4 bytes
_numitems        4 bytes
_nummissiles     4 bytes
_nobjects        4 bytes
[NUMLEVELS × (glSeedTbl:4 + gnLevelTypeTbl:4)]   NUMLEVELS = 17 (non-Hellfire) or 25 (Hellfire)
```
= **179 bytes total header** (non-Hellfire, NUMLEVELS=17), then immediately:

```
raw PlayerStruct dump, natural alignment, minus its trailing 10 pointers
```

(the loader does `memcpy(&plr[i], buf, sizeof(PlayerStruct) - 10*sizeof(void*))`
— the omitted trailer is 9 `unsigned char*` + 1 `void*`, all animation-data
pointers, harmless to leave as zero/garbage).

Empirically verified offsets within that raw `PlayerStruct` blob (offset 0
= first byte after the 179-byte header):

- `_pName` (32-byte char array): struct-relative offset **320**
- `_pGold`: struct-relative offset **460**
- `InvBody[7]` (equipped items, `ItemStruct[7]`): struct-relative offset
  **892**
- `InvList[40]` (backpack grid, `ItemStruct[40]`): struct-relative offset
  **3468**
- `_pNumInv`: struct-relative offset **18188**
- `InvGrid[40]` (1 byte each): struct-relative offset **18192**

These offsets assume **8-byte alignment for `unsigned __int64` fields**
(there are several earlier in the struct — spell bitmasks) — confirmed
correct against empirical marker positions (verify against your own build's
struct if these ever look off by a few bytes; the alignment rule is the
single most fragile assumption here). Equipment slot order in `InvBody`:
`HEAD, RING_LEFT, RING_RIGHT, AMULET, HAND_LEFT, HAND_RIGHT, CHEST`.

### Raw `ItemStruct` layout (368 bytes, non-Hellfire, natural alignment)

Verified field-by-field against a real exported item (`.ITM` file from an
old-school memory-injection trainer/editor combo — format is a small custom
header + a raw dump of exactly this struct). Struct-relative byte offsets:

| Field | Offset | Size | Notes |
|---|---|---|---|
| `_iSeed` | 0 | 4 | for "always-unique" base items, equals the unique-table index directly |
| `_iCreateInfo` | 4 | 2 (WORD) | bit0-5 level, bit6 ONLYGOOD, bit7 UPER15, bit8 UPER1, bit9 UNIQUE, bit10-14 TOWN(smith/premium/boy/witch/healer), bit15 PREGEN |
| `_itype` | 8 | 4 | item_type enum (0=MISC..14=FOOD; armor=8 MARMOR/etc.) |
| `_iAnimData` (ptr) | 24 | 4 | zero when not actively rendered |
| `_iIdentified` | 56 | 4 (BOOL) | |
| `_iMagical` | 60 | 1 | 0 normal / 1 magic / 2 unique |
| `_iName` | 61 | 64 | display name (base) |
| `_iIName` | 125 | 64 | display name (identified/unique override) |
| `_iLoc` | 189 | 1 | equip-slot type enum (0 none/1 onehand/2 twohand/3 armor/4 helm/5 ring/6 amulet/7 unequipable/8 belt) |
| `_iClass` | 190 | 1 | item_class enum |
| `_iCurs` | 192 | 4 | cursor/graphic index |
| `_ivalue` | 196 | 4 | base gold value |
| `_iIvalue` | 200 | 4 | identified/unique gold value |
| `_iMinDam`/`_iMaxDam` | 204/208 | 4 each | |
| `_iAC` | 212 | 4 | final armor class (post unique-bonus) |
| `_iFlags` | 216 | 4 | item_special_effect bitmask |
| `_iMiscId` | 220 | 4 | item_misc_id enum (0x1B = IMISC_UNIQUE for "always unique" items) |
| `_iSpell` | 224 | 4 | |
| `_iCharges`/`_iMaxCharges` | 228/232 | 4 each | |
| `_iDurability`/`_iMaxDur` | 236/240 | 4 each | |
| `_iPLVit` | 268 | 4 | (one of many `_iPL*` unique/magic bonus fields, offsets follow struct declaration order at 4 bytes apart from `_iPLDam` at 244) |
| `_iUid` | 308 | 4 | unique-item-table index, set post-construction by `GetUniqueItem` |
| `_iMinStr` | 352 | 1 | |
| `_iMinMag` | 353 | 1 (unsigned) | |
| `_iMinDex` | 354 | 1 | |
| `_iStatFlag` | 356 | 4 (BOOL) | whether player meets equip requirements |
| `IDidx` | 360 | 4 | base item-table index (`_item_indexes` enum — same numbering as compact save format's `idx`) |

Total struct size 368 bytes (non-Hellfire — Hellfire adds one trailing
`int _iDamAcFlags` field, 372 bytes total; detect via the `game` file's
magic word, `'HELF'` vs `'RETL'`).

## 5. Compact save format (`hero` file — `PkPlayerStruct`/`PkItemStruct`)

`#pragma pack(push,1)` — no padding anywhere, unlike §4's natural-alignment
structs. Much smaller per-item footprint (19 bytes vs 368).

`PkItemStruct` (19 bytes):
```
iSeed         DWORD (4)
iCreateInfo   WORD  (2)
idx           WORD  (2)   -- same numbering as raw ItemStruct's IDidx
bId           BYTE  (1)   = iIdentified + 2*iMagical
bDur          BYTE  (1)
bMDur         BYTE  (1)
bCh           BYTE  (1)
bMCh          BYTE  (1)
wValue        WORD  (2)   -- only meaningful for gold stacks
dwBuff        DWORD (4)   -- only meaningful for ears (PvP trophies)
```
Empty slot marker: `idx == 0xFFFF`.

`PkPlayerStruct` total size: **1266 bytes** (verified empirically — decodes
to exactly this length). Key field offsets from struct start:
- Player name (32 bytes): offset 12
- `InvBody[7]` (`PkItemStruct[7]`, equip slots, same order as §4): offset 124
- `InvList[40]` (`PkItemStruct[40]`): offset 257
- `InvGrid[40]` (1 byte each): offset 1017
- `_pNumInv` (1 byte): offset 1057
- `SpdList[8]` (belt, `PkItemStruct[8]`): offset 1058

Reconstruction on load: game code walks `idx` through the same
`_item_indexes` table used by `IDidx` above, calls `GetItemAttrs` to pull
base stats, then — **for items whose base type has intrinsic
`_iMiscId == IMISC_UNIQUE`** (the ~20-ish named quest-reward uniques like
Arkaine's Valor, not randomly-rolled uniques) — uses `iSeed` **directly** as
the index into the unique-item table (`GetUniqueItem(ii, iSeed)`), bypassing
the normal random-roll-for-uniqueness logic entirely. This is a
frequently-missed special case: for these items, `iSeed` is *not* a PRNG
seed, it's a direct table index.

## 6. Item identity data

- Base item type index (`IDI_*` enum / `IDidx` / compact-format `idx`):
  positional, matches declaration order in `_item_indexes` enum
  (`enums.h`) and `AllItemsList` (`itemdat.cpp`) — index 0 = gold, and so
  on. Verified against a real 1990s-era working item editor's own
  documented ID table (independent confirmation, not just from source).
  **Stable across vanilla/Hellfire** for all items that exist in vanilla —
  Hellfire only appends new indices at the end.
- Unique-item table index (`_iUid` / compact-format `iSeed` for
  intrinsically-unique items): positional index into `UniqueItemList[]`
  array (`itemdat.cpp`). **This array's order is NOT the same as the
  `UITYPE_*` enum's numeric values** — it's grouped by "famous quest items
  first," not enum order or alphabetical. Always derive this by counting
  array position directly from source, don't assume it matches any enum
  value.
- Cross-reference table worth building once, up front, for the tool: every
  `(IDidx, name, iLoc, iClass, iCurs)` from `AllItemsList`, and every
  `(UniqueItemList position, name, stat bonuses)` from `UniqueItemList`.
  This session only needed one item (Arkaine's Valor, `IDidx=28`,
  unique-table position `7`) but a general tool wants the whole table.

## 7. Inventory grid placement encoding (compact format `InvGrid`, and the
## raw format's `InvGrid` is identical in concept)

For placing an item into the backpack grid (not equipped) rather than an
equip slot:
- Item footprint in cells comes from `InvItemWidth[]`/`InvItemHeight[]`
  (`cursor.cpp`), indexed by `_iCurs + CURSOR_FIRSTITEM` (`CURSOR_FIRSTITEM
  = 0xC`), divided by 28px per cell. (Diablo 1 body armor is consistently
  2×3 cells.)
- Grid is 40 cells, stored as 4 rows × 10 columns, linear index
  `row*10 + col`.
- To place an item: find a free (all-zero) `w×h` block. Write the new item
  into `InvList[_pNumInv]` (0-based, pre-increment), then increment
  `_pNumInv`. Every occupied cell of the new item gets a **negative** marker
  equal to `-newPNumInv`, except the bottom-right-most cell of the item's
  footprint, which gets the **positive** marker `+newPNumInv`. Reading back:
  `InvList` index = `abs(marker) - 1`.

## 8. Suggested tool architecture

Split into a format/logic core (no UI dependencies at all) and a GUI shell
on top, so the two can be developed and tested independently — the core is
where all the fragile byte-offset work lives, and it should be fully
exercisable (and unit-testable against a real save) without the GUI running.

**Core** (Python is comfortable here given how much of this was prototyped
in Python already; a compiled language only matters if performance becomes
an issue, which it won't for save-file-sized data):

- `mpq.py` — archive read (mpyq-based, `listfile=False`) + the minimal
  surgical patch/write path from §1 (no full rebuild, ever).
- `dcodec.py` — the game's SHA1 stream cipher (§3), both directions.
- `blast` — thin wrapper around a compiled `blast.c` for DCL decompression
  (no encoder needed given the single-unit-uncompressed write strategy).
- `pkplayer.py` — compact `hero` format read/write (§5): items and
  character stats.
- `rawplayer.py` — raw `game` format read/write (§4), including the
  big-endian header parsing: items and character stats.
- `character.py` — a unified in-memory character model (stats + equipment +
  backpack + belt) that the GUI actually binds to, with methods to
  load-from/save-to both `hero` and `game` representations at once so the
  two-file duality (§4) is handled once, centrally, rather than by every
  call site remembering to touch both.
- `itemdata.py` — the `AllItemsList`/`UniqueItemList` reference tables
  (§6), generated once from `devilution`'s `itemdat.cpp` rather than
  hand-transcribed.

**GUI shell:** thin, sits entirely on top of `character.py`. Rough shape
regardless of eventual framework choice: open a save → character
summary/stats panel (editable fields) → equipment paperdoll + backpack grid
(editable slots, presumably via a picker populated from `itemdata.py`) →
save/write-back. Framework choice deferred per §0 — build against the core
with a minimal CLI first to shake out format bugs before investing in UI.

### Open questions / likely next issues for a general-purpose tool

- Hellfire support: struct sizes and `NUMLEVELS` differ (see notes above);
  the `game` magic word disambiguates at runtime.
- Level files (`perml00`..`perml16` etc.) store dungeon-floor state
  (monsters, ground items, tile flags) separately from player state — not
  needed for inventory editing, but relevant if the tool ever wants to
  place items **on the ground** rather than in inventory/equipped.
- Multiplayer/closed-Battle.net saves use a different password
  (`GetComputerName`-derived) — out of scope unless requested.
- No implode *encoder* was implemented or needed this session (see §1's
  single-unit workaround) — if file-size bloat from uncompressed storage
  ever matters, implementing a real PKWARE implode encoder would close
  that gap, but it's a substantial undertaking on its own.
