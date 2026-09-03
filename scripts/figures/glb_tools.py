"""Stdlib-only GLB tools shared by the figure build and the figure CI gate.

A GLB is a 12-byte header, a JSON chunk and one binary chunk; accessors are
typed arrays inside that chunk. Everything the character pipeline needs to
inspect or finish a figure — reading, pruning, rewriting, decoding the sheet
PNG, evaluating a clip's forward kinematics to measure a stride — fits in
this file without a dependency, so the gate runs in CI, in pre-commit and
inside Blender's own interpreter alike (docs/agent-society/character-pipeline.md §10).
"""

from __future__ import annotations

import json
import math
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942

COMPONENT_FORMATS = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
COMPONENT_MAX = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}
TYPE_COUNTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]
Mat4 = tuple[float, ...]  # 16 floats, column-major like glTF


@dataclass
class Glb:
    doc: dict
    blob: bytes
    path: Path | None = None
    warnings: list[str] = field(default_factory=list)


def read_glb(path: Path | str) -> Glb:
    data = Path(path).read_bytes()
    if len(data) < 12:
        raise ValueError(f"{path}: too short to be a GLB")
    magic, _version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise ValueError(f"{path}: not a GLB (magic {magic:#x})")
    offset = 12
    doc: dict | None = None
    blob = b""
    while offset + 8 <= min(length, len(data)):
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        payload = data[offset + 8 : offset + 8 + chunk_len]
        if chunk_type == JSON_CHUNK:
            doc = json.loads(payload.decode("utf-8"))
        elif chunk_type == BIN_CHUNK:
            blob = bytes(payload)
        offset += 8 + chunk_len
    if doc is None:
        raise ValueError(f"{path}: GLB without a JSON chunk")
    return Glb(doc=doc, blob=blob, path=Path(path))


def write_glb(path: Path | str, doc: dict, blob: bytes) -> None:
    json_bytes = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    bin_bytes = blob + b"\x00" * (-len(blob) % 4)
    if doc.get("buffers"):
        doc["buffers"][0]["byteLength"] = len(blob)
        json_bytes = json.dumps(doc, separators=(",", ":")).encode("utf-8")
        json_bytes += b" " * (-len(json_bytes) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    out = bytearray()
    out += struct.pack("<III", GLB_MAGIC, 2, total)
    out += struct.pack("<II", len(json_bytes), JSON_CHUNK) + json_bytes
    out += struct.pack("<II", len(bin_bytes), BIN_CHUNK) + bin_bytes
    Path(path).write_bytes(bytes(out))


# ---------------------------------------------------------------------------
# accessors
# ---------------------------------------------------------------------------


def accessor_values(glb: Glb, index: int) -> list[tuple[float, ...]]:
    """Every element of an accessor as a tuple of floats (normalized ints scaled)."""
    acc = glb.doc["accessors"][index]
    count = acc["count"]
    comps = TYPE_COUNTS[acc["type"]]
    fmt = COMPONENT_FORMATS[acc["componentType"]]
    size = struct.calcsize(fmt)
    if "bufferView" not in acc:
        return [tuple(0.0 for _ in range(comps)) for _ in range(count)]
    view = glb.doc["bufferViews"][acc["bufferView"]]
    stride = view.get("byteStride", comps * size)
    base = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    scale = COMPONENT_MAX.get(acc["componentType"]) if acc.get("normalized") else None
    out: list[tuple[float, ...]] = []
    for i in range(count):
        raw = struct.unpack_from("<" + fmt * comps, glb.blob, base + i * stride)
        if scale:
            out.append(tuple(max(v / scale, -1.0) for v in raw))
        else:
            out.append(tuple(float(v) for v in raw))
    return out


def accessor_ints(glb: Glb, index: int) -> list[tuple[int, ...]]:
    acc = glb.doc["accessors"][index]
    comps = TYPE_COUNTS[acc["type"]]
    fmt = COMPONENT_FORMATS[acc["componentType"]]
    size = struct.calcsize(fmt)
    view = glb.doc["bufferViews"][acc["bufferView"]]
    stride = view.get("byteStride", comps * size)
    base = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    return [
        tuple(int(v) for v in struct.unpack_from("<" + fmt * comps, glb.blob, base + i * stride))
        for i in range(acc["count"])
    ]


def image_bytes(glb: Glb, image_index: int) -> bytes:
    img = glb.doc["images"][image_index]
    view = glb.doc["bufferViews"][img["bufferView"]]
    start = view.get("byteOffset", 0)
    return glb.blob[start : start + view["byteLength"]]


# ---------------------------------------------------------------------------
# appending and collapsing — the finish step's only writes into the blob
# ---------------------------------------------------------------------------


def append_accessor(glb: Glb, values: list[tuple[float, ...]], gltf_type: str) -> int:
    """Append float data as a new bufferView + accessor; return the accessor index."""
    comps = TYPE_COUNTS[gltf_type]
    payload = b"".join(struct.pack("<" + "f" * comps, *v) for v in values)
    blob = bytearray(glb.blob)
    blob += b"\x00" * (-len(blob) % 4)
    offset = len(blob)
    blob += payload
    glb.blob = bytes(blob)
    doc = glb.doc
    doc.setdefault("bufferViews", []).append(
        {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
    )
    acc: dict = {
        "bufferView": len(doc["bufferViews"]) - 1,
        "componentType": 5126,
        "count": len(values),
        "type": gltf_type,
    }
    if values:
        acc["min"] = [min(v[k] for v in values) for k in range(comps)]
        acc["max"] = [max(v[k] for v in values) for k in range(comps)]
    doc.setdefault("accessors", []).append(acc)
    return len(doc["accessors"]) - 1


def collapse_constant_channels(glb: Glb, epsilon: float = 1e-6) -> int:
    """Rewrite every channel whose value never changes as two keys (start, end).

    A baked export carries a key per frame for bones that never move; the
    pose must survive (a constant is not necessarily the rest pose), the
    bytes need not. Returns the number of channels collapsed.
    """
    doc = glb.doc
    collapsed = 0
    for anim in doc.get("animations", []):
        for ch in anim["channels"]:
            sampler = anim["samplers"][ch["sampler"]]
            values = accessor_values(glb, sampler["output"])
            if len(values) <= 2:
                continue
            first = values[0]
            if any(abs(a - b) > epsilon for v in values for a, b in zip(v, first, strict=True)):
                continue
            times = accessor_values(glb, sampler["input"])
            span = [(times[0][0],), (times[-1][0],)]
            gltf_type = doc["accessors"][sampler["output"]]["type"]
            new_sampler = {
                "input": append_accessor(glb, span, "SCALAR"),
                "output": append_accessor(glb, [first, first], gltf_type),
                "interpolation": "LINEAR",
            }
            anim["samplers"].append(new_sampler)
            ch["sampler"] = len(anim["samplers"]) - 1
            collapsed += 1
    return collapsed


# ---------------------------------------------------------------------------
# pruning — drop accessors/bufferViews nothing references, rebuild the blob
# ---------------------------------------------------------------------------


def prune_unused(glb: Glb) -> Glb:
    doc = glb.doc
    used_acc: set[int] = set()
    for mesh in doc.get("meshes", []):
        for prim in mesh["primitives"]:
            used_acc.update(prim["attributes"].values())
            if "indices" in prim:
                used_acc.add(prim["indices"])
            for target in prim.get("targets", []):
                used_acc.update(target.values())
    for skin in doc.get("skins", []):
        if "inverseBindMatrices" in skin:
            used_acc.add(skin["inverseBindMatrices"])
    for anim in doc.get("animations", []):
        for sampler in anim["samplers"]:
            used_acc.add(sampler["input"])
            used_acc.add(sampler["output"])
    acc_order = sorted(used_acc)
    acc_remap = {old: new for new, old in enumerate(acc_order)}

    used_views: set[int] = set()
    for i in acc_order:
        acc = doc["accessors"][i]
        if "bufferView" in acc:
            used_views.add(acc["bufferView"])
    for img in doc.get("images", []):
        if "bufferView" in img:
            used_views.add(img["bufferView"])
    view_order = sorted(used_views)
    view_remap = {old: new for new, old in enumerate(view_order)}

    new_blob = bytearray()
    new_views = []
    for old in view_order:
        view = dict(doc["bufferViews"][old])
        start = view.get("byteOffset", 0)
        chunk = glb.blob[start : start + view["byteLength"]]
        new_blob += b"\x00" * (-len(new_blob) % 4)
        view["byteOffset"] = len(new_blob)
        view["buffer"] = 0
        new_blob += chunk
        new_views.append(view)

    new_accessors = []
    for old in acc_order:
        acc = dict(doc["accessors"][old])
        if "bufferView" in acc:
            acc["bufferView"] = view_remap[acc["bufferView"]]
        new_accessors.append(acc)

    for mesh in doc.get("meshes", []):
        for prim in mesh["primitives"]:
            prim["attributes"] = {k: acc_remap[v] for k, v in prim["attributes"].items()}
            if "indices" in prim:
                prim["indices"] = acc_remap[prim["indices"]]
            if "targets" in prim:
                prim["targets"] = [{k: acc_remap[v] for k, v in t.items()} for t in prim["targets"]]
    for skin in doc.get("skins", []):
        if "inverseBindMatrices" in skin:
            skin["inverseBindMatrices"] = acc_remap[skin["inverseBindMatrices"]]
    for anim in doc.get("animations", []):
        for sampler in anim["samplers"]:
            sampler["input"] = acc_remap[sampler["input"]]
            sampler["output"] = acc_remap[sampler["output"]]
    for img in doc.get("images", []):
        if "bufferView" in img:
            img["bufferView"] = view_remap[img["bufferView"]]

    doc["accessors"] = new_accessors
    doc["bufferViews"] = new_views
    doc["buffers"] = [{"byteLength": len(new_blob)}]
    return Glb(doc=doc, blob=bytes(new_blob), path=glb.path, warnings=glb.warnings)


# ---------------------------------------------------------------------------
# PNG — enough of the format for an 8-bit RGB/RGBA sheet
# ---------------------------------------------------------------------------


def decode_png(data: bytes) -> tuple[int, int, int, list[bytes]]:
    """Return (width, height, channels, rows) for a non-interlaced 8-bit RGB/RGBA PNG."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    width = height = 0
    channels = 0
    idat = bytearray()
    while pos + 8 <= len(data):
        length, ctype = struct.unpack_from(">I4s", data, pos)
        payload = data[pos + 8 : pos + 8 + length]
        if ctype == b"IHDR":
            width, height, depth, color, _c, _f, interlace = struct.unpack(">IIBBBBB", payload)
            if depth != 8 or interlace != 0:
                raise ValueError("only 8-bit non-interlaced PNGs are supported")
            channels = {2: 3, 6: 4, 0: 1, 4: 2}.get(color, 0)
            if channels == 0:
                raise ValueError(f"unsupported PNG colour type {color}")
        elif ctype == b"IDAT":
            idat += payload
        elif ctype == b"IEND":
            break
        pos += 12 + length
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    rows: list[bytes] = []
    prev = bytearray(stride)
    offset = 0
    for _ in range(height):
        filt = raw[offset]
        line = bytearray(raw[offset + 1 : offset + 1 + stride])
        offset += 1 + stride
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if filt == 1:
                line[i] = (line[i] + a) & 0xFF
            elif filt == 2:
                line[i] = (line[i] + b) & 0xFF
            elif filt == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        rows.append(bytes(line))
        prev = line
    return width, height, channels, rows


def encode_png(width: int, height: int, rows: list[bytes], channels: int = 4) -> bytes:
    color = {1: 0, 2: 4, 3: 2, 4: 6}[channels]

    def chunk(ctype: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)

    raw = b"".join(b"\x00" + row for row in rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def png_size(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    width, height = struct.unpack_from(">II", data, 16)
    return width, height


# ---------------------------------------------------------------------------
# matrices and node hierarchy
# ---------------------------------------------------------------------------


def mat_identity() -> Mat4:
    return (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


def mat_mul(a: Mat4, b: Mat4) -> Mat4:
    """Column-major a @ b."""
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
    return tuple(out)


def mat_from_trs(t: Vec3, r: Quat, s: Vec3) -> Mat4:
    x, y, z, w = r
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    m = [
        (1 - 2 * (yy + zz)) * s[0],
        (2 * (xy + wz)) * s[0],
        (2 * (xz - wy)) * s[0],
        0.0,
        (2 * (xy - wz)) * s[1],
        (1 - 2 * (xx + zz)) * s[1],
        (2 * (yz + wx)) * s[1],
        0.0,
        (2 * (xz + wy)) * s[2],
        (2 * (yz - wx)) * s[2],
        (1 - 2 * (xx + yy)) * s[2],
        0.0,
        t[0],
        t[1],
        t[2],
        1.0,
    ]
    return tuple(m)


def mat_point(m: Mat4, p: Vec3) -> Vec3:
    return (
        m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12],
        m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
        m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14],
    )


def node_local_trs(node: dict) -> tuple[Vec3, Quat, Vec3]:
    if "matrix" in node:
        m = node["matrix"]
        t = (m[12], m[13], m[14])
        sx = math.sqrt(m[0] ** 2 + m[1] ** 2 + m[2] ** 2)
        sy = math.sqrt(m[4] ** 2 + m[5] ** 2 + m[6] ** 2)
        sz = math.sqrt(m[8] ** 2 + m[9] ** 2 + m[10] ** 2)
        # rotation from the normalized 3x3 (good enough for validation)
        r = _quat_from_mat3(
            [
                m[0] / sx,
                m[1] / sx,
                m[2] / sx,
                m[4] / sy,
                m[5] / sy,
                m[6] / sy,
                m[8] / sz,
                m[9] / sz,
                m[10] / sz,
            ]
        )
        return t, r, (sx, sy, sz)
    t = tuple(node.get("translation", [0.0, 0.0, 0.0]))
    r = tuple(node.get("rotation", [0.0, 0.0, 0.0, 1.0]))
    s = tuple(node.get("scale", [1.0, 1.0, 1.0]))
    return t, r, s  # type: ignore[return-value]


def _quat_from_mat3(m: list[float]) -> Quat:
    # m is column-major 3x3: m[col*3+row]
    m00, m10, m20, m01, m11, m21, m02, m12, m22 = m
    trace = m00 + m11 + m22
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        return ((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
    if m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        return (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    if m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        return ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    s = math.sqrt(1.0 + m22 - m00 - m11) * 2
    return ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)


def parent_map(doc: dict) -> dict[int, int | None]:
    parents: dict[int, int | None] = {i: None for i in range(len(doc.get("nodes", [])))}
    for i, node in enumerate(doc.get("nodes", [])):
        for child in node.get("children", []):
            parents[child] = i
    return parents


def node_index_by_name(doc: dict) -> dict[str, int]:
    return {n.get("name", ""): i for i, n in enumerate(doc.get("nodes", []))}


def world_matrices(doc: dict, local: dict[int, Mat4] | None = None) -> list[Mat4]:
    """World matrix per node from the given local matrices (rest pose when None)."""
    nodes = doc.get("nodes", [])
    parents = parent_map(doc)
    cache: dict[int, Mat4] = {}

    def local_of(i: int) -> Mat4:
        if local is not None and i in local:
            return local[i]
        t, r, s = node_local_trs(nodes[i])
        return mat_from_trs(t, r, s)

    def world(i: int) -> Mat4:
        if i in cache:
            return cache[i]
        p = parents[i]
        m = local_of(i) if p is None else mat_mul(world(p), local_of(i))
        cache[i] = m
        return m

    return [world(i) for i in range(len(nodes))]


# ---------------------------------------------------------------------------
# animation sampling
# ---------------------------------------------------------------------------


def clip_by_name(doc: dict, name: str) -> dict | None:
    for anim in doc.get("animations", []):
        if anim.get("name") == name:
            return anim
    return None


def clip_duration(glb: Glb, anim: dict) -> float:
    end = 0.0
    for sampler in anim["samplers"]:
        acc = glb.doc["accessors"][sampler["input"]]
        if "max" in acc:
            end = max(end, float(acc["max"][0]))
        else:
            times = accessor_values(glb, sampler["input"])
            if times:
                end = max(end, times[-1][0])
    return end


def _lerp(a: tuple[float, ...], b: tuple[float, ...], f: float) -> tuple[float, ...]:
    return tuple(x + (y - x) * f for x, y in zip(a, b, strict=True))


def _nlerp(a: Quat, b: Quat, f: float) -> Quat:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    if dot < 0:
        b = tuple(-v for v in b)  # type: ignore[assignment]
    q = _lerp(a, b, f)
    n = math.sqrt(sum(v * v for v in q)) or 1.0
    return tuple(v / n for v in q)  # type: ignore[return-value]


def sample_clip(glb: Glb, anim: dict, t: float) -> dict[int, tuple[Vec3, Quat, Vec3]]:
    """Local TRS per animated node at time t (rest values fill the untouched parts)."""
    nodes = glb.doc["nodes"]
    state: dict[int, list] = {}
    for channel in anim["channels"]:
        target = channel["target"]
        if "node" not in target:
            continue
        idx = target["node"]
        if idx not in state:
            state[idx] = list(node_local_trs(nodes[idx]))
        sampler = anim["samplers"][channel["sampler"]]
        times = [v[0] for v in accessor_values(glb, sampler["input"])]
        values = accessor_values(glb, sampler["output"])
        if not times:
            continue
        interp = sampler.get("interpolation", "LINEAR")
        if interp == "CUBICSPLINE":
            values = values[1::3]  # the in-tangent/value/out-tangent triplets → values
        if t <= times[0]:
            v = values[0]
        elif t >= times[-1]:
            v = values[-1]
        else:
            k = 0
            while k + 1 < len(times) and times[k + 1] < t:
                k += 1
            f = (t - times[k]) / (times[k + 1] - times[k]) if times[k + 1] > times[k] else 0.0
            if interp == "STEP":
                v = values[k]
            elif target["path"] == "rotation":
                v = _nlerp(values[k], values[k + 1], f)  # type: ignore[arg-type]
            else:
                v = _lerp(values[k], values[k + 1], f)
        slot = {"translation": 0, "rotation": 1, "scale": 2}.get(target["path"])
        if slot is not None:
            state[idx][slot] = tuple(v)
    return {i: (tuple(s[0]), tuple(s[1]), tuple(s[2])) for i, s in state.items()}  # type: ignore[misc]


def joint_positions_over_clip(
    glb: Glb, anim: dict, joint_names: list[str], samples: int = 48
) -> dict[str, list[Vec3]]:
    names = node_index_by_name(glb.doc)
    duration = clip_duration(glb, anim)
    out: dict[str, list[Vec3]] = {n: [] for n in joint_names}
    for k in range(samples):
        t = duration * k / samples
        trs = sample_clip(glb, anim, t)
        local = {i: mat_from_trs(*v) for i, v in trs.items()}
        worlds = world_matrices(glb.doc, local)
        for n in joint_names:
            if n in names:
                m = worlds[names[n]]
                out[n].append((m[12], m[13], m[14]))
    return out


def measure_stride(glb: Glb, anim: dict, feet: list[str], samples: int = 48) -> float:
    """Ground distance one loop covers: twice the mean forward (Z) excursion of the feet."""
    tracks = joint_positions_over_clip(glb, anim, feet, samples)
    excursions = []
    for name in feet:
        zs = [p[2] for p in tracks.get(name, [])]
        if zs:
            excursions.append(max(zs) - min(zs))
    if not excursions:
        return 0.0
    return 2.0 * sum(excursions) / len(excursions)


# ---------------------------------------------------------------------------
# mesh facts
# ---------------------------------------------------------------------------


def triangle_count(doc: dict, mesh: dict) -> int:
    total = 0
    for prim in mesh["primitives"]:
        mode = prim.get("mode", 4)
        if mode != 4:
            continue
        if "indices" in prim:
            total += doc["accessors"][prim["indices"]]["count"] // 3
        else:
            total += doc["accessors"][prim["attributes"]["POSITION"]]["count"] // 3
    return total


def mesh_bounds(doc: dict, mesh: dict) -> tuple[Vec3, Vec3]:
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for prim in mesh["primitives"]:
        acc = doc["accessors"][prim["attributes"]["POSITION"]]
        for k in range(3):
            lo[k] = min(lo[k], acc["min"][k])
            hi[k] = max(hi[k], acc["max"][k])
    return (lo[0], lo[1], lo[2]), (hi[0], hi[1], hi[2])


def skinned_mesh_nodes(doc: dict) -> list[int]:
    return [i for i, n in enumerate(doc.get("nodes", [])) if "mesh" in n and "skin" in n]


def figure_extras(doc: dict) -> dict | None:
    return (doc.get("asset", {}).get("extras") or {}).get("jarvis_figure")


def clips_extras(doc: dict) -> dict | None:
    """A clip library: an archetype's skeleton and its clips, with no body.

    Nine clips of a 23-bone rig are 200 KB — five times the geometry of a
    procedural body. Shipping them once and letting every body of that rig
    borrow them is what makes a new look cost 40 KB instead of 240 KB.
    """
    return (doc.get("asset", {}).get("extras") or {}).get("jarvis_clips")


def part_extras(doc: dict) -> dict | None:
    return (doc.get("asset", {}).get("extras") or {}).get("jarvis_part")
