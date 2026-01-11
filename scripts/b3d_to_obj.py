import os
import struct
import sys

# --- Configuration ---
# True for smooth shading, False for flat shading
WRITE_NORMALS = True

# --- Helper Functions ---


def read_float(f):
    return struct.unpack('<f', f.read(4))[0]


def read_uint32(f):
    return struct.unpack('<I', f.read(4))[0]


def read_uint16(f):
    return struct.unpack('<H', f.read(2))[0]


def read_uint8(f):
    return struct.unpack('<B', f.read(1))[0]

# --- Logic Functions ---


def read_b3d_header(f):
    version_check = read_uint32(f)
    version_float = read_float(f)

    vertex_flags = 0
    if version_float > 0.5:
        vertex_flags = read_uint32(f)

    bbox_min = [read_float(f) for _ in range(3)]
    bbox_max = [read_float(f) for _ in range(3)]
    color_flags = read_uint8(f)

    return version_check, vertex_flags, bbox_min, bbox_max, color_flags


def decode_packed_normal(packed):
    x_bits = packed & 0x1FF
    y_bits = (packed >> 10) & 0x1FF
    z_bits = (packed >> 20) & 0x1FF

    def decode_component(bits):
        if bits & 0x200:
            val_unsigned = bits | 0xFFFFFE00
            val_signed = val_unsigned - 0x100000000
            return float(val_signed) * 0.0019531
        else:
            return float(bits) / 511.0

    return decode_component(x_bits), decode_component(y_bits), decode_component(z_bits)


def read_vertex_pntc(f, vertex_flags):
    # 1. Position (12 bytes)
    pos = (read_float(f), read_float(f), read_float(f))

    # 2. UVs
    if vertex_flags & 4:
        # Compressed UV (2 uint16s)
        u_val = read_uint16(f)
        v_val = read_uint16(f)
        u = u_val / 65535.0
        v = v_val / 65535.0
    else:
        # Uncompressed UV (2 floats)
        u = read_float(f)
        v = read_float(f)

    # 3. Normals
    if vertex_flags & 1:
        # Compressed Normal (4 bytes)
        normal = decode_packed_normal(read_uint32(f))
    else:
        # Uncompressed Normal (12 bytes)
        normal = (read_float(f), read_float(f), read_float(f))

    # Flip V to match OBJ format
    v = 1.0 - v

    return {'position': pos, 'texcoord': (u, v), 'normal': normal}


def read_indices(f, index_count, vertex_flags):
    indices = []
    is_byte = vertex_flags & 2
    for _ in range(index_count):
        indices.append(read_uint8(f) if is_byte else read_uint16(f))
    return indices


def read_colors(f, vertex_count, vertices, color_flags):
    try:
        if f.tell() + (vertex_count * 2) > os.fstat(f.fileno()).st_size:
            return

        for i in range(vertex_count):
            c = read_uint16(f)
            if color_flags & 1:  # 4444 ARGB
                color = ((c & 0xF) << 20) | (((c >> 4) & 0xF) << 12) | ((c >> 8) & 0xF0) | ((c << 16) & 0xF0000000)
            else:  # 565 RGB
                color = ((c & 0x1F) << 19) | ((c*32) & 0xFC00) | ((c >> 11) & 0xF8) | 0xFF000000
            vertices[i]['color'] = color
    except Exception:
        pass

# --- Main Conversion Logic ---


def convert_b3d_to_obj(b3d_path, obj_path, verbose=False):
    print(f"Converting {os.path.basename(b3d_path)}...")
    try:
        with open(b3d_path, 'rb') as f:
            v_check, vertex_flags, bbox_min, bbox_max, color_flags = read_b3d_header(f)

            vertex_count = read_uint32(f)
            vertices = [read_vertex_pntc(f, vertex_flags) for _ in range(vertex_count)]

            index_count = read_uint32(f)
            indices = read_indices(f, index_count, vertex_flags)

            # Try to read optional colors
            if f.tell() < os.fstat(f.fileno()).st_size:
                try:
                    if read_uint8(f):
                        read_colors(f, vertex_count, vertices, color_flags)
                except:
                    pass

        # Create output directory only if specified
        output_dir_path = os.path.dirname(obj_path)
        if output_dir_path:
            os.makedirs(output_dir_path, exist_ok=True)

        with open(obj_path, 'w') as obj:
            obj.write(f"# Converted from {os.path.basename(b3d_path)}\n")

            # Write Positions
            for v in vertices:
                p = v['position']
                obj.write(f"v {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}\n")

            # Write UVs
            for v in vertices:
                uv = v['texcoord']
                obj.write(f"vt {uv[0]:.6f} {uv[1]:.6f}\n")

            # Write Normals
            if WRITE_NORMALS:
                for v in vertices:
                    n = v['normal']
                    obj.write(f"vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}\n")

            # Write Faces
            for i in range(0, len(indices), 3):
                if i+2 < len(indices):
                    f = [indices[i+j]+1 for j in range(3)]
                    if WRITE_NORMALS:
                        obj.write(f"f {f[0]}/{f[0]}/{f[0]} {f[1]}/{f[1]}/{f[1]} {f[2]}/{f[2]}/{f[2]}\n")
                    else:
                        obj.write(f"f {f[0]}/{f[0]} {f[1]}/{f[1]} {f[2]}/{f[2]}\n")
        print(f"  -> Saved to {os.path.basename(obj_path)}")

    except Exception as e:
        print(f"  FAILED: {e}")
        if verbose:
            import traceback
            traceback.print_exc()


# --- Entry Point ---
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  Batch:  python script.py foldername")
        print("  Single: python script.py file.b3d")
        sys.exit(1)

    path = sys.argv[1]
    verbose = '-v' in sys.argv

    if os.path.isfile(path):
        convert_b3d_to_obj(path, path.replace('.b3d', '.obj'), verbose)

    elif os.path.isdir(path):
        input_dir = path.rstrip(os.sep)
        output_dir = input_dir + "_obj"

        print(f"Batch Processing: {input_dir}")
        print(f"Output Folder:    {output_dir}")
        print("-" * 30)

        for root, dirs, files in os.walk(input_dir):
            for name in files:
                if name.lower().endswith('.b3d'):
                    input_file = os.path.join(root, name)

                    rel_path = os.path.relpath(input_file, input_dir)
                    output_file = os.path.join(output_dir, rel_path.replace('.b3d', '.obj'))

                    convert_b3d_to_obj(input_file, output_file, verbose)

        print("-" * 30)
        print("Batch conversion complete.")
