import os
import struct
import zlib

DAT_FILE = "content.dat"
EXTRACT_ROOT = "content"
NUM_HEADER_BYTES = 16

os.makedirs(EXTRACT_ROOT, exist_ok=True)

# ---- hash function ----
def get_hash(s: str) -> int:
    if not s:
        return 0
    v3 = ord(s[0])
    v6 = len(s)
    result = 0
    for c in map(ord, s[1:] + "\x00"):
        result = (result + v6 * v3) & 0xFFFFFFFF
        v3 = c
        v6 = (18000 * (v6 & 0xFFFF) + (v6 >> 16)) & 0xFFFFFFFF
    return result

# ---- decompression function ----
def decompress_multi_stream(compressed_payload, expected_size):
    decompressed = b""
    rest = compressed_payload
    while rest and len(decompressed) < expected_size:
        dobj = zlib.decompressobj()
        chunk = dobj.decompress(rest, expected_size - len(decompressed))
        decompressed += chunk
        rest = dobj.unconsumed_tail
        if not rest and len(decompressed) < expected_size:
            idx = compressed_payload.find(b'\x78', len(decompressed))
            if idx != -1:
                rest = compressed_payload[idx:]
    decompressed += dobj.flush()
    return decompressed[:expected_size]


# ---- load candidate filenames ----
candidates = []
with open("strings.txt", "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            candidates.append(line)

# Build hash to filename mapping
hash_to_name = {}
for cand in candidates:
    h = get_hash(cand)
    hash_to_name[h] = cand

# ---- read entries from DAT file ----
entries = []
with open(DAT_FILE, "rb") as f:
    header = f.read(12)
    magic, file_count, entry_size = struct.unpack("<4sII", header)
    print(f"Magic: {magic.hex()}")
    print(f"File count: {file_count}")
    print(f"Entry size: {entry_size}")

    for _ in range(file_count):
        raw = f.read(entry_size)
        size, offset, file_hash = struct.unpack("<III", raw[:12])
        entries.append((size, offset, file_hash))

# Calculate metadata end position
meta_end = 12 + file_count * entry_size

# ---- extract all files ----
with open(DAT_FILE, "rb") as f, open("headers.txt", "w") as header_out:
    for idx, (size, offset, file_hash) in enumerate(entries):
        # Check if we can resolve the hash
        resolved_name = hash_to_name.get(file_hash)

        # Seek to file data, offset is relative to end of metadata
        f.seek(meta_end + offset)

        # Read file header
        file_header = f.read(8)
        file_size_field = struct.unpack("<I", file_header[:4])[0]
        header_magic = file_header[4:8]

        is_compressed = header_magic == b'\x01\xDE\xC0\xDE'

        # Read and decompress data
        if is_compressed:
            compressed_payload = f.read(file_size_field)
            try:
                data_to_save = decompress_multi_stream(compressed_payload, size)
            except zlib.error as e:
                print(f"Error decompressing file {idx} (hash {file_hash:08X}): {e}")
                data_to_save = compressed_payload
        else:
            data_to_save = f.read(file_size_field)[:size]

        # Determine output path and filename
        if resolved_name:
            rel_path = resolved_name.lstrip("/")
            out_path = os.path.join(EXTRACT_ROOT, rel_path)
            out_dir = os.path.dirname(out_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            print(f"File {idx}: RESOLVED '{resolved_name}' (hash {file_hash:08X})")
        else:
            # Use hash as filename
            filename = f"{idx}_{file_hash:08X}"
            out_path = os.path.join(EXTRACT_ROOT, filename)
            print(f"File {idx}: UNRESOLVED (hash {file_hash:08X}) -> saved as {filename}")

        # Write file
        with open(out_path, "wb") as out_f:
            out_f.write(data_to_save)

        # Dump header bytes
        header_bytes = data_to_save[:NUM_HEADER_BYTES]
        hex_bytes = " ".join(f"{b:02X}" for b in header_bytes)
        header_out.write(f"{out_path}: {hex_bytes}\n")

        print(f"  -> Extracted to {out_path} (size={size}, compressed={is_compressed})")

print(f"\nExtraction complete! Files saved to {EXTRACT_ROOT}/")
