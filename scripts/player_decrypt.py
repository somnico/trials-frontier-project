import zlib

# Key and multiplier for XOR
KEY = 157
MULTIPLIER = 8377

with open("dl_content.state", "rb") as f:
    encrypted_data = bytearray(f.read())

# Decryption logic
def xor_decrypt(data, key, multiplier):
    result = bytearray(data)
    current_key = key
    for i in range(len(result)):
        result[i] ^= current_key & 0xFF  # XOR only on the lowest byte
        current_key *= multiplier
        current_key &= 0xFFFFFFFF  # Account for 32-bit overflow
    return result


# XOR decryption
decrypted_data = xor_decrypt(encrypted_data, KEY, MULTIPLIER)

# Skip header for decompression
header = decrypted_data[:16]
body_to_decompress = decrypted_data[16:]

# Attempt decompression
decompressed_data = None
try:
    decompressed_data = zlib.decompress(body_to_decompress)
except zlib.error:
    try:
        decompressed_data = zlib.decompress(body_to_decompress, wbits=-15)
    except zlib.error as e:
        print(f"Decompression failed, keeping raw decrypted bytes: {e}")
        decompressed_data = body_to_decompress

# Reattach header if needed
output_data = header + decrypted_data

# Save output
with open("dl_contesnt.state", "wb") as f:
    f.write(output_data)

print("Decryption (and decompression) complete. Output: player_decrypt.bin")
print(output_data[:64].hex())
