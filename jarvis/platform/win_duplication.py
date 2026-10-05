"""Windows: read a monitor's framebuffer at full depth (Desktop Duplication, FP16).

GDI, mss and 8-bit Windows Graphics Capture hand back an SDR copy of an HDR
desktop: highlights clip and the wide gamut is squeezed into sRGB. Desktop
Duplication with ``DuplicateOutput1`` and an FP16 format returns what the
compositor scans out — scRGB, linear light, BT.709 primaries with values
beyond 0..1, where 1.0 is 80 nits.

Two consumers:

* :meth:`DesktopDuplication.read_linear` — one full frame as an FP16 array,
  for screenshots.
* :meth:`DesktopDuplication.read_p010` — a crop, scaled to the video size and
  converted ON THE GPU to BT.2020 PQ 10-bit 4:2:0 (P010 planes), with the
  mouse pointer drawn in. A 4K frame never crosses the bus as FP16, which is
  what keeps 60 FPS recording possible.

Windows-only by construction (D3D11/DXGI through ctypes, no extra package);
importing elsewhere is harmless and :func:`available` says no. Windows that
:class:`exclude_from_capture <jarvis.platform.capture_exclusion>` excludes
stay out of these frames, exactly as with Windows Graphics Capture.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_DXGI_FORMAT_R16G16B16A16_FLOAT = 10
_DXGI_FORMAT_R16G16_UNORM = 35
_DXGI_FORMAT_R16_UNORM = 56
_DXGI_FORMAT_R8G8B8A8_UNORM = 28
_D3D11_USAGE_DEFAULT = 0
_D3D11_USAGE_STAGING = 3
_D3D11_BIND_CONSTANT_BUFFER = 0x4
_D3D11_BIND_SHADER_RESOURCE = 0x8
_D3D11_BIND_UNORDERED_ACCESS = 0x80
_D3D11_CPU_ACCESS_READ = 0x20000
_D3D11_MAP_READ = 1
_D3D_FEATURE_LEVEL_11_0 = 0xB000
_D3D11_SDK_VERSION = 7
_D3D_DRIVER_TYPE_UNKNOWN = 0
_D3D11_FILTER_MIN_MAG_MIP_LINEAR = 0x15
_D3D11_TEXTURE_ADDRESS_CLAMP = 3
_DXGI_ERROR_NOT_FOUND = -2005270526  # 0x887A0002
_DXGI_ERROR_WAIT_TIMEOUT = -2005270489  # 0x887A0027
_DXGI_ERROR_ACCESS_LOST = -2005270490  # 0x887A0026
_ROTATION_IDENTITY = (0, 1)  # UNSPECIFIED, IDENTITY
_POINTER_MONOCHROME, _POINTER_COLOR, _POINTER_MASKED = 1, 2, 4

# vtable slots (IUnknown = 0..2)
_QUERY_INTERFACE, _RELEASE = 0, 2
_FACTORY1_ENUM_ADAPTERS1 = 12
_ADAPTER_ENUM_OUTPUTS = 7
_OUTPUT_GET_DESC = 7
_OUTPUT5_DUPLICATE_OUTPUT1 = 26
_OUTPUT6_GET_DESC1 = 27
_DUP_GET_DESC, _DUP_ACQUIRE, _DUP_POINTER_SHAPE, _DUP_RELEASE_FRAME = 7, 8, 11, 14
_DEV_CREATE_BUFFER, _DEV_CREATE_TEXTURE2D = 3, 5
_DEV_CREATE_SRV, _DEV_CREATE_UAV = 7, 8
_DEV_CREATE_COMPUTE_SHADER, _DEV_CREATE_SAMPLER = 18, 23
_DEV_GET_IMMEDIATE_CONTEXT = 40
_CTX_MAP, _CTX_UNMAP, _CTX_DISPATCH = 14, 15, 41
_CTX_COPY_RESOURCE, _CTX_UPDATE_SUBRESOURCE = 47, 48
_CTX_CS_SRV, _CTX_CS_UAV, _CTX_CS_SHADER, _CTX_CS_SAMPLERS, _CTX_CS_CBUFFERS = 67, 68, 69, 70, 71
_BLOB_POINTER, _BLOB_SIZE = 3, 4

#: Linear scRGB in, BT.2020 PQ limited-range P010 out (Y plane + interleaved
#: CbCr at half resolution). One thread per 2x2 block. The pointer is drawn
#: before conversion, at SDR white, so it looks like the one on screen.
_SHADER = r"""
Texture2D<float4> Desk : register(t0);
Texture2D<float4> Cur : register(t1);
SamplerState Lin : register(s0);
RWTexture2D<float> OutY : register(u0);
RWTexture2D<float2> OutUV : register(u1);
cbuffer Params : register(b0) {
    float4 Crop;        // x, y, w, h in desktop pixels
    float4 Sizes;       // desktop w, h, output w, h
    float4 CursorRect;  // x, y, w, h in desktop pixels; w == 0: no pointer
    float4 Scales;      // x: scRGB value of SDR white
};
static const float3x3 To2020 = {
    0.627404, 0.329283, 0.043313,
    0.069097, 0.919540, 0.011362,
    0.016391, 0.088013, 0.895595 };
static const float3 Luma = float3(0.2627, 0.6780, 0.0593);

float3 Pq(float3 l) {
    float3 y = saturate(max(l, 0.0) * (80.0 / 10000.0));
    float3 p = pow(y, 0.1593017578125);
    return pow((0.8359375 + 18.8515625 * p) / (1.0 + 18.6875 * p), 78.84375);
}

float3 SrgbToLinear(float3 c) {
    float3 lo = c / 12.92;
    float3 hi = pow((c + 0.055) / 1.055, 2.4);
    return float3(c.r <= 0.04045 ? lo.r : hi.r,
                  c.g <= 0.04045 ? lo.g : hi.g,
                  c.b <= 0.04045 ? lo.b : hi.b);
}

float3 Fetch(float2 px) {
    float2 pos = Crop.xy + (px + 0.5) * Crop.zw / Sizes.zw;
    float3 c = Desk.SampleLevel(Lin, pos / Sizes.xy, 0).rgb;
    if (CursorRect.z > 0) {
        float2 q = pos - CursorRect.xy;
        if (q.x >= 0 && q.y >= 0 && q.x < CursorRect.z && q.y < CursorRect.w) {
            float4 k = Cur.Load(int3(int2(q), 0));
            if (abs(k.a * 255.0 - 1.0) < 0.5) {
                // An inverting pointer pixel: the opposite of what is below.
                float l = dot(c, float3(0.2126, 0.7152, 0.0722)) / Scales.x;
                c = (l < 0.5 ? 1.0 : 0.0) * Scales.x;
            } else {
                c = lerp(c, SrgbToLinear(k.rgb) * Scales.x, k.a);
            }
        }
    }
    return mul(To2020, c);
}

[numthreads(8, 8, 1)]
void main(uint3 id : SV_DispatchThreadID) {
    uint2 half = uint2(Sizes.zw) / 2;
    if (id.x >= half.x || id.y >= half.y) return;
    float3 acc = 0.0;
    [unroll] for (uint i = 0; i < 4; i++) {
        uint2 p = id.xy * 2 + uint2(i & 1, i >> 1);
        float3 e = Pq(Fetch(float2(p)));
        OutY[p] = round(64.0 + 876.0 * dot(e, Luma)) * 64.0 / 65535.0;
        acc += e;
    }
    acc *= 0.25;
    float yc = dot(acc, Luma);
    float cb = round(512.0 + 896.0 * (acc.b - yc) / 1.8814);
    float cr = round(512.0 + 896.0 * (acc.r - yc) / 1.4746);
    OutUV[id.xy] = float2(cb, cr) * 64.0 / 65535.0;
}
"""


class DuplicationUnavailable(RuntimeError):
    """This monitor cannot be read at full depth here; use the SDR path."""


class AccessLost(DuplicationUnavailable):
    """The desktop switched (mode change, secure desktop, full-screen app)."""


@dataclass(frozen=True, slots=True)
class OutputInfo:
    device_name: str
    #: Desktop rectangle in physical pixels: left, top, right, bottom.
    rect: tuple[int, int, int, int]
    bits_per_color: int
    #: DXGI_COLOR_SPACE_TYPE; 12 = RGB full G2084 P2020 (HDR), 0 = sRGB.
    color_space: int
    max_nits: float
    max_full_frame_nits: float
    min_nits: float

    @property
    def hdr(self) -> bool:
        return self.color_space == 12

    @property
    def size(self) -> tuple[int, int]:
        return self.rect[2] - self.rect[0], self.rect[3] - self.rect[1]


def available() -> bool:
    return sys.platform == "win32"


class _GUID(ctypes.Structure):
    _fields_ = [
        ("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16),
        ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, text: str) -> _GUID:
        import uuid  # noqa: PLC0415

        raw = uuid.UUID(text).bytes_le
        guid = cls()
        ctypes.memmove(ctypes.byref(guid), raw, 16)
        return guid


_IID_FACTORY1 = _GUID.parse("770aae78-f26f-4dba-a829-253c83d1b387")
_IID_OUTPUT5 = _GUID.parse("80a07424-ab52-42eb-833c-0c42fd282d98")
_IID_OUTPUT6 = _GUID.parse("068346e8-aaec-4b84-add7-137f513f77a1")
_IID_TEXTURE2D = _GUID.parse("6f15aaf2-d208-4e89-9ab4-489535d34f9c")


class _OutputDesc(ctypes.Structure):
    _fields_ = [
        ("name", wintypes.WCHAR * 32), ("rect", wintypes.RECT),
        ("attached", wintypes.BOOL), ("rotation", ctypes.c_uint32),
        ("monitor", wintypes.HMONITOR),
    ]


class _OutputDesc1(ctypes.Structure):
    _fields_ = [
        ("name", wintypes.WCHAR * 32), ("rect", wintypes.RECT),
        ("attached", wintypes.BOOL), ("rotation", ctypes.c_uint32),
        ("monitor", wintypes.HMONITOR), ("bits", ctypes.c_uint32),
        ("color_space", ctypes.c_uint32), ("primaries", ctypes.c_float * 8),
        ("min_nits", ctypes.c_float), ("max_nits", ctypes.c_float),
        ("max_full_frame_nits", ctypes.c_float),
    ]


class _ModeDesc(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
        ("refresh_num", ctypes.c_uint32), ("refresh_den", ctypes.c_uint32),
        ("format", ctypes.c_uint32), ("scanline", ctypes.c_uint32),
        ("scaling", ctypes.c_uint32),
    ]


class _DuplDesc(ctypes.Structure):
    _fields_ = [("mode", _ModeDesc), ("rotation", ctypes.c_uint32), ("in_memory", wintypes.BOOL)]


class _PointerPosition(ctypes.Structure):
    _fields_ = [("position", wintypes.POINT), ("visible", wintypes.BOOL)]


class _FrameInfo(ctypes.Structure):
    _fields_ = [
        ("last_present", ctypes.c_int64), ("last_mouse", ctypes.c_int64),
        ("accumulated", ctypes.c_uint32), ("coalesced", wintypes.BOOL),
        ("protected_masked", wintypes.BOOL), ("pointer", _PointerPosition),
        ("metadata_size", ctypes.c_uint32), ("pointer_shape_size", ctypes.c_uint32),
    ]


class _PointerShapeInfo(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32), ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32), ("pitch", ctypes.c_uint32),
        ("hotspot", wintypes.POINT),
    ]


class _Texture2DDesc(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
        ("mips", ctypes.c_uint32), ("array", ctypes.c_uint32),
        ("format", ctypes.c_uint32), ("samples", ctypes.c_uint32),
        ("quality", ctypes.c_uint32), ("usage", ctypes.c_uint32),
        ("bind", ctypes.c_uint32), ("cpu", ctypes.c_uint32), ("misc", ctypes.c_uint32),
    ]


class _BufferDesc(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32), ("usage", ctypes.c_uint32), ("bind", ctypes.c_uint32),
        ("cpu", ctypes.c_uint32), ("misc", ctypes.c_uint32), ("stride", ctypes.c_uint32),
    ]


class _SubresourceData(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("pitch", ctypes.c_uint32), ("slice", ctypes.c_uint32)]


class _Mapped(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("pitch", ctypes.c_uint32), ("depth", ctypes.c_uint32)]


class _SamplerDesc(ctypes.Structure):
    _fields_ = [
        ("filter", ctypes.c_uint32), ("u", ctypes.c_uint32), ("v", ctypes.c_uint32),
        ("w", ctypes.c_uint32), ("lod_bias", ctypes.c_float),
        ("anisotropy", ctypes.c_uint32), ("compare", ctypes.c_uint32),
        ("border", ctypes.c_float * 4), ("min_lod", ctypes.c_float), ("max_lod", ctypes.c_float),
    ]


class _Params(ctypes.Structure):
    _fields_ = [
        ("crop", ctypes.c_float * 4), ("sizes", ctypes.c_float * 4),
        ("cursor", ctypes.c_float * 4), ("scales", ctypes.c_float * 4),
    ]


class _Com:
    """A raw COM pointer with vtable calls by slot; :meth:`release` exactly once."""

    __slots__ = ("ptr",)

    def __init__(self, ptr: int | None) -> None:
        if not ptr:
            raise DuplicationUnavailable("A graphics object could not be created.")
        self.ptr = ptr

    def call(self, slot: int, *args: Any, restype: Any = ctypes.c_long) -> Any:
        vtable = ctypes.cast(
            ctypes.cast(self.ptr, ctypes.POINTER(ctypes.c_void_p))[0],
            ctypes.POINTER(ctypes.c_void_p),
        )
        prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *map(_argtype, args))
        return prototype(vtable[slot])(self.ptr, *args)

    def check(self, slot: int, *args: Any, what: str) -> None:
        hr = self.call(slot, *args)
        if hr < 0:
            _raise(hr, what)

    def query(self, iid: _GUID) -> _Com:
        out = ctypes.c_void_p()
        hr = self.call(_QUERY_INTERFACE, ctypes.pointer(iid), ctypes.pointer(out))
        if hr < 0:
            _raise(hr, "query interface")
        return _Com(out.value)

    def release(self) -> None:
        if self.ptr:
            self.call(_RELEASE, restype=ctypes.c_ulong)
            self.ptr = 0


def _argtype(arg: Any) -> Any:
    """Plain numbers keep their C type; everything pointer-like goes as ``void*``.

    ``c_void_p`` accepts ``None``, ``byref()``, arrays and pointer instances.
    """
    if isinstance(arg, ctypes._SimpleCData) and not isinstance(  # noqa: SLF001
        arg, (ctypes.c_void_p, ctypes.c_char_p)
    ):
        return type(arg)
    return ctypes.c_void_p


def _raise(hr: int, what: str) -> None:
    if hr == _DXGI_ERROR_ACCESS_LOST:
        raise AccessLost(f"Desktop duplication lost access during {what}.")
    raise DuplicationUnavailable(f"{what} failed (HRESULT 0x{hr & 0xFFFFFFFF:08X}).")


def _out() -> ctypes.c_void_p:
    return ctypes.c_void_p()


def _compile_shader() -> bytes:
    compiler = ctypes.WinDLL("d3dcompiler_47")
    source = _SHADER.encode("ascii")
    code, errors = ctypes.c_void_p(), ctypes.c_void_p()
    compiler.D3DCompile.restype = ctypes.c_long
    hr = compiler.D3DCompile(
        source, ctypes.c_size_t(len(source)), b"appshot_p010", None, None,
        b"main", b"cs_5_0", ctypes.c_uint(1 << 15), ctypes.c_uint(0),  # OPTIMIZATION_LEVEL3
        ctypes.byref(code), ctypes.byref(errors),
    )
    if errors.value:
        blob = _Com(errors.value)
        size = blob.call(_BLOB_SIZE, restype=ctypes.c_size_t)
        text = ctypes.string_at(blob.call(_BLOB_POINTER, restype=ctypes.c_void_p), size)
        blob.release()
        if hr < 0:
            raise DuplicationUnavailable(f"Shader compile failed: {text.decode(errors='replace')}")
    if hr < 0 or not code.value:
        _raise(hr, "shader compile")
    blob = _Com(code.value)
    try:
        size = blob.call(_BLOB_SIZE, restype=ctypes.c_size_t)
        return ctypes.string_at(blob.call(_BLOB_POINTER, restype=ctypes.c_void_p), size)
    finally:
        blob.release()


class DesktopDuplication:
    """One monitor, duplicated at FP16. Not thread-safe: use from one thread.

    ``device_name`` is the monitor's GDI name (``\\\\.\\DISPLAY1``); ``None``
    takes the first monitor on the first adapter. Raises
    :class:`DuplicationUnavailable` when the monitor cannot be duplicated
    (rotated, remote session, no D3D11) — callers fall back to SDR capture.
    """

    def __init__(self, device_name: str | None = None) -> None:
        if not available():
            raise DuplicationUnavailable("Desktop duplication exists only on Windows.")
        self._objects: list[_Com] = []
        self._dup: _Com | None = None
        self._desktop: _Com | None = None
        self._desktop_srv: _Com | None = None
        self._has_frame = False
        self._pointer_pos = (0, 0)
        self._pointer_visible = False
        self._pointer_size = (0, 0)
        self._pointer_srv: _Com | None = None
        self._pointer_tex: _Com | None = None
        self._p010: dict[str, Any] = {}
        self._shader: _Com | None = None
        try:
            self._open(device_name)
        except BaseException:
            self.close()
            raise

    # -- setup ---------------------------------------------------------------
    def _keep(self, obj: _Com) -> _Com:
        self._objects.append(obj)
        return obj

    def _open(self, device_name: str | None) -> None:
        from jarvis.core.win32_dpi import per_monitor_dpi_context  # noqa: PLC0415

        dxgi = ctypes.WinDLL("dxgi")
        d3d11 = ctypes.WinDLL("d3d11")
        factory_ptr = _out()
        hr = dxgi.CreateDXGIFactory1(ctypes.byref(_IID_FACTORY1), ctypes.byref(factory_ptr))
        if hr < 0:
            _raise(hr, "CreateDXGIFactory1")
        factory = self._keep(_Com(factory_ptr.value))
        adapter, output, desc = self._find_output(factory, device_name)
        level = ctypes.c_uint32(_D3D_FEATURE_LEVEL_11_0)
        device_ptr, context_ptr = _out(), _out()
        hr = d3d11.D3D11CreateDevice(
            ctypes.c_void_p(adapter.ptr), _D3D_DRIVER_TYPE_UNKNOWN, None, 0,
            ctypes.byref(level), 1, _D3D11_SDK_VERSION,
            ctypes.byref(device_ptr), None, ctypes.byref(context_ptr),
        )
        if hr < 0:
            _raise(hr, "D3D11CreateDevice")
        self._device = self._keep(_Com(device_ptr.value))
        self._context = self._keep(_Com(context_ptr.value))
        self._output5 = self._keep(output.query(_IID_OUTPUT5))
        info = self._describe(output, desc)
        self.info = info
        formats = (ctypes.c_uint32 * 1)(_DXGI_FORMAT_R16G16B16A16_FLOAT)
        dup = _out()
        # DuplicateOutput1 refuses a DPI-unaware thread.
        with per_monitor_dpi_context():
            hr = self._output5.call(
                _OUTPUT5_DUPLICATE_OUTPUT1, ctypes.c_void_p(self._device.ptr),
                ctypes.c_uint(0), ctypes.c_uint(1), formats, ctypes.byref(dup),
            )
        if hr < 0:
            _raise(hr, "DuplicateOutput1")
        self._dup = _Com(dup.value)
        dupl = _DuplDesc()
        self._dup.call(_DUP_GET_DESC, ctypes.byref(dupl), restype=None)
        if dupl.rotation not in _ROTATION_IDENTITY:
            raise DuplicationUnavailable("Rotated monitors are recorded with the SDR path.")
        self.size = (int(dupl.mode.width), int(dupl.mode.height))
        self.refresh_hz = (
            dupl.mode.refresh_num / dupl.mode.refresh_den if dupl.mode.refresh_den else 0.0
        )
        self._desktop = self._texture(
            self.size, _DXGI_FORMAT_R16G16B16A16_FLOAT, bind=_D3D11_BIND_SHADER_RESOURCE
        )
        self._desktop_srv = self._view(_DEV_CREATE_SRV, self._desktop)

    def _find_output(self, factory: _Com, device_name: str | None):
        wanted = (device_name or "").casefold()
        index = 0
        while True:
            adapter_ptr = _out()
            hr = factory.call(
                _FACTORY1_ENUM_ADAPTERS1, ctypes.c_uint(index), ctypes.byref(adapter_ptr)
            )
            if hr == _DXGI_ERROR_NOT_FOUND:
                break
            if hr < 0:
                _raise(hr, "EnumAdapters1")
            adapter = _Com(adapter_ptr.value)
            slot = 0
            found = None
            while True:
                output_ptr = _out()
                hr = adapter.call(
                    _ADAPTER_ENUM_OUTPUTS, ctypes.c_uint(slot), ctypes.byref(output_ptr)
                )
                if hr < 0:
                    break
                output = _Com(output_ptr.value)
                desc = _OutputDesc()
                output.check(_OUTPUT_GET_DESC, ctypes.byref(desc), what="output description")
                if found is None and (not wanted or desc.name.casefold() == wanted):
                    found = (output, desc)
                else:
                    output.release()
                slot += 1
            if found is not None:
                self._keep(adapter)
                self._keep(found[0])
                return adapter, found[0], found[1]
            adapter.release()
            index += 1
        raise DuplicationUnavailable(f"No graphics output drives {device_name or 'a monitor'}.")

    def _describe(self, output: _Com, desc: _OutputDesc) -> OutputInfo:
        rect = (desc.rect.left, desc.rect.top, desc.rect.right, desc.rect.bottom)
        try:
            output6 = output.query(_IID_OUTPUT6)
        except DuplicationUnavailable:
            return OutputInfo(desc.name, rect, 8, 0, 0.0, 0.0, 0.0)
        try:
            d1 = _OutputDesc1()
            output6.check(_OUTPUT6_GET_DESC1, ctypes.byref(d1), what="output description")
        finally:
            output6.release()
        return OutputInfo(
            desc.name, rect, int(d1.bits), int(d1.color_space),
            float(d1.max_nits), float(d1.max_full_frame_nits), float(d1.min_nits),
        )

    def _texture(
        self, size: tuple[int, int], fmt: int, *, bind: int = 0, staging: bool = False,
        initial: tuple[Any, int] | None = None,
    ) -> _Com:
        desc = _Texture2DDesc(
            width=size[0], height=size[1], mips=1, array=1, format=fmt, samples=1,
            usage=_D3D11_USAGE_STAGING if staging else _D3D11_USAGE_DEFAULT,
            bind=0 if staging else bind, cpu=_D3D11_CPU_ACCESS_READ if staging else 0,
        )
        data = None
        if initial is not None:
            data = ctypes.byref(_SubresourceData(initial[0], initial[1], 0))
        out = _out()
        self._device.check(
            _DEV_CREATE_TEXTURE2D, ctypes.byref(desc), data, ctypes.byref(out), what="texture"
        )
        return self._keep(_Com(out.value))

    def _view(self, slot: int, resource: _Com) -> _Com:
        out = _out()
        self._device.check(
            slot, ctypes.c_void_p(resource.ptr), None, ctypes.byref(out), what="resource view"
        )
        return self._keep(_Com(out.value))

    # -- frames --------------------------------------------------------------
    def acquire(self, timeout_ms: int = 0) -> bool:
        """Take the newest desktop image if one arrived. True when it changed.

        The previous image stays readable when nothing new arrived, so a
        static screen keeps recording the same picture.
        """
        if self._dup is None:
            raise AccessLost("The duplication is closed.")
        info = _FrameInfo()
        resource = _out()
        hr = self._dup.call(
            _DUP_ACQUIRE, ctypes.c_uint(max(0, int(timeout_ms))),
            ctypes.byref(info), ctypes.byref(resource),
        )
        if hr == _DXGI_ERROR_WAIT_TIMEOUT:
            return False
        if hr < 0:
            _raise(hr, "AcquireNextFrame")
        changed = False
        try:
            if info.last_mouse:
                self._pointer_visible = bool(info.pointer.visible)
                self._pointer_pos = (int(info.pointer.position.x), int(info.pointer.position.y))
            if info.pointer_shape_size:
                self._read_pointer_shape(int(info.pointer_shape_size))
            # Only a presented frame holds the desktop: the very first acquire
            # of an FP16 duplication returns an all-black surface.
            if info.last_present:
                texture = _Com(resource.value).query(_IID_TEXTURE2D)
                try:
                    self._context.call(
                        _CTX_COPY_RESOURCE, ctypes.c_void_p(self._desktop.ptr),
                        ctypes.c_void_p(texture.ptr), restype=None,
                    )
                finally:
                    texture.release()
                self._has_frame = True
                changed = True
        finally:
            if resource.value:
                _Com(resource.value).release()
            self._dup.call(_DUP_RELEASE_FRAME)
        return changed or bool(info.last_mouse)

    def wait_first_frame(self, timeout_ms: int = 1000) -> None:
        """Block until the desktop image is readable (the first presented frame).

        A desktop where nothing moves presents nothing; then this raises and the
        caller takes the SDR path instead of waiting longer.
        """
        import time  # noqa: PLC0415

        deadline = time.monotonic() + timeout_ms / 1000.0
        while not self._has_frame:
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                raise DuplicationUnavailable("The desktop image did not arrive in time.")
            self.acquire(min(remaining, 100))

    def read_linear(self) -> Any:
        """The whole monitor as ``float16`` ``(height, width, 4)`` scRGB (1.0 = 80 nits)."""
        import numpy as np  # noqa: PLC0415

        self.wait_first_frame()
        staging = self._texture(self.size, _DXGI_FORMAT_R16G16B16A16_FLOAT, staging=True)
        try:
            self._context.call(
                _CTX_COPY_RESOURCE, ctypes.c_void_p(staging.ptr),
                ctypes.c_void_p(self._desktop.ptr), restype=None,
            )
            return self._read_back(staging, self.size, np.float16, 4)
        finally:
            self._objects.remove(staging)
            staging.release()

    def read_p010(
        self,
        crop: tuple[int, int, int, int],
        out_size: tuple[int, int],
        *,
        sdr_white: float = 1.0,
        draw_pointer: bool = True,
    ) -> tuple[Any, Any]:
        """``crop`` (x, y, w, h, monitor pixels) as BT.2020 PQ P010 planes of ``out_size``.

        Returns ``(y, uv)``: ``uint16`` arrays of shape ``(h, w)`` and
        ``(h/2, w/2, 2)``, 10-bit codes in the high bits as P010 stores them.
        ``sdr_white`` is the scRGB value of SDR white, used for the pointer.
        """
        import numpy as np  # noqa: PLC0415

        width, height = out_size
        if width % 2 or height % 2 or width < 2 or height < 2:
            raise ValueError("P010 needs an even output size.")
        self.wait_first_frame()
        planes = self._p010_targets(out_size)
        params = _Params()
        params.crop[:] = [float(v) for v in crop]
        params.sizes[:] = [float(self.size[0]), float(self.size[1]), float(width), float(height)]
        if draw_pointer and self._pointer_visible and self._pointer_srv is not None:
            params.cursor[:] = [
                float(self._pointer_pos[0]), float(self._pointer_pos[1]),
                float(self._pointer_size[0]), float(self._pointer_size[1]),
            ]
        params.scales[0] = float(sdr_white)
        ctx = self._context
        ctx.call(
            _CTX_UPDATE_SUBRESOURCE, ctypes.c_void_p(planes["cbuffer"].ptr), ctypes.c_uint(0),
            None, ctypes.byref(params), ctypes.c_uint(0), ctypes.c_uint(0), restype=None,
        )
        pointer_srv = self._pointer_srv.ptr if self._pointer_srv is not None else None
        srvs = (ctypes.c_void_p * 2)(self._desktop_srv.ptr, pointer_srv)
        uavs = (ctypes.c_void_p * 2)(planes["y_uav"].ptr, planes["uv_uav"].ptr)
        samplers = (ctypes.c_void_p * 1)(planes["sampler"].ptr)
        buffers = (ctypes.c_void_p * 1)(planes["cbuffer"].ptr)
        ctx.call(_CTX_CS_SHADER, ctypes.c_void_p(self._shader.ptr), None, ctypes.c_uint(0),
                 restype=None)
        ctx.call(_CTX_CS_SRV, ctypes.c_uint(0), ctypes.c_uint(2), srvs, restype=None)
        ctx.call(_CTX_CS_UAV, ctypes.c_uint(0), ctypes.c_uint(2), uavs, None, restype=None)
        ctx.call(_CTX_CS_SAMPLERS, ctypes.c_uint(0), ctypes.c_uint(1), samplers, restype=None)
        ctx.call(_CTX_CS_CBUFFERS, ctypes.c_uint(0), ctypes.c_uint(1), buffers, restype=None)
        ctx.call(
            _CTX_DISPATCH, ctypes.c_uint((width // 2 + 7) // 8),
            ctypes.c_uint((height // 2 + 7) // 8), ctypes.c_uint(1), restype=None,
        )
        # Unbind the outputs so the copies below see finished writes.
        none2 = (ctypes.c_void_p * 2)(None, None)
        ctx.call(_CTX_CS_UAV, ctypes.c_uint(0), ctypes.c_uint(2), none2, None, restype=None)
        ctx.call(_CTX_CS_SRV, ctypes.c_uint(0), ctypes.c_uint(2), none2, restype=None)
        for name in ("y", "uv"):
            ctx.call(
                _CTX_COPY_RESOURCE, ctypes.c_void_p(planes[f"{name}_stage"].ptr),
                ctypes.c_void_p(planes[name].ptr), restype=None,
            )
        y = self._read_back(planes["y_stage"], (width, height), np.uint16, 1)[..., 0]
        uv = self._read_back(planes["uv_stage"], (width // 2, height // 2), np.uint16, 2)
        return y, uv

    def _p010_targets(self, size: tuple[int, int]) -> dict[str, Any]:
        if self._p010.get("size") == size:
            return self._p010
        if self._shader is None:
            code = _compile_shader()
            out = _out()
            self._device.check(
                _DEV_CREATE_COMPUTE_SHADER, ctypes.c_char_p(code), ctypes.c_size_t(len(code)),
                None, ctypes.byref(out), what="compute shader",
            )
            self._shader = self._keep(_Com(out.value))
        for key in ("y", "uv", "y_uav", "uv_uav", "y_stage", "uv_stage"):
            obj = self._p010.pop(key, None)
            if obj is not None:
                self._objects.remove(obj)
                obj.release()
        width, height = size
        half = (width // 2, height // 2)
        targets = self._p010
        targets["y"] = self._texture(
            size, _DXGI_FORMAT_R16_UNORM, bind=_D3D11_BIND_UNORDERED_ACCESS
        )
        targets["uv"] = self._texture(
            half, _DXGI_FORMAT_R16G16_UNORM, bind=_D3D11_BIND_UNORDERED_ACCESS
        )
        targets["y_uav"] = self._view(_DEV_CREATE_UAV, targets["y"])
        targets["uv_uav"] = self._view(_DEV_CREATE_UAV, targets["uv"])
        targets["y_stage"] = self._texture(size, _DXGI_FORMAT_R16_UNORM, staging=True)
        targets["uv_stage"] = self._texture(half, _DXGI_FORMAT_R16G16_UNORM, staging=True)
        if "sampler" not in targets:
            desc = _SamplerDesc(
                filter=_D3D11_FILTER_MIN_MAG_MIP_LINEAR, u=_D3D11_TEXTURE_ADDRESS_CLAMP,
                v=_D3D11_TEXTURE_ADDRESS_CLAMP, w=_D3D11_TEXTURE_ADDRESS_CLAMP,
                anisotropy=1, compare=1, max_lod=3.402823e38,
            )
            out = _out()
            self._device.check(
                _DEV_CREATE_SAMPLER, ctypes.byref(desc), ctypes.byref(out), what="sampler"
            )
            targets["sampler"] = self._keep(_Com(out.value))
            buffer_desc = _BufferDesc(
                size=ctypes.sizeof(_Params), usage=_D3D11_USAGE_DEFAULT,
                bind=_D3D11_BIND_CONSTANT_BUFFER,
            )
            out = _out()
            self._device.check(
                _DEV_CREATE_BUFFER, ctypes.byref(buffer_desc), None, ctypes.byref(out),
                what="constant buffer",
            )
            targets["cbuffer"] = self._keep(_Com(out.value))
        targets["size"] = size
        return targets

    def _read_back(self, staging: _Com, size: tuple[int, int], dtype: Any, channels: int) -> Any:
        import numpy as np  # noqa: PLC0415

        mapped = _Mapped()
        self._context.check(
            _CTX_MAP, ctypes.c_void_p(staging.ptr), ctypes.c_uint(0),
            ctypes.c_uint(_D3D11_MAP_READ), ctypes.c_uint(0), ctypes.byref(mapped),
            what="Map",
        )
        try:
            width, height = size
            item = np.dtype(dtype).itemsize * channels
            raw = (ctypes.c_ubyte * (mapped.pitch * height)).from_address(mapped.data)
            rows = np.frombuffer(raw, dtype=np.uint8).reshape(height, mapped.pitch)
            return (
                rows[:, : width * item].copy().view(dtype).reshape(height, width, channels)
            )
        finally:
            self._context.call(
                _CTX_UNMAP, ctypes.c_void_p(staging.ptr), ctypes.c_uint(0), restype=None
            )

    def _read_pointer_shape(self, size: int) -> None:
        import numpy as np  # noqa: PLC0415

        buffer = (ctypes.c_ubyte * size)()
        required = ctypes.c_uint()
        shape = _PointerShapeInfo()
        hr = self._dup.call(
            _DUP_POINTER_SHAPE, ctypes.c_uint(size), buffer,
            ctypes.byref(required), ctypes.byref(shape),
        )
        if hr < 0:
            return  # Keep the previous pointer; a missing shape is cosmetic.
        rgba = pointer_rgba(bytes(buffer), shape.type, shape.width, shape.height, shape.pitch)
        if rgba is None:
            return
        for attr in ("_pointer_srv", "_pointer_tex"):
            old = getattr(self, attr)
            if old is not None:
                self._objects.remove(old)
                old.release()
                setattr(self, attr, None)
        height, width = rgba.shape[:2]
        data = np.ascontiguousarray(rgba)
        self._pointer_tex = self._texture(
            (width, height), _DXGI_FORMAT_R8G8B8A8_UNORM, bind=_D3D11_BIND_SHADER_RESOURCE,
            initial=(data.ctypes.data, width * 4),
        )
        self._pointer_srv = self._view(_DEV_CREATE_SRV, self._pointer_tex)
        self._pointer_size = (width, height)

    def close(self) -> None:
        """Release the duplication first, then every object in reverse order."""
        if self._dup is not None:
            try:
                self._dup.release()
            except OSError:
                log.debug("duplication release failed", exc_info=True)
            self._dup = None
        for obj in reversed(self._objects):
            try:
                obj.release()
            except OSError:
                log.debug("graphics object release failed", exc_info=True)
        self._objects.clear()

    def __enter__(self) -> DesktopDuplication:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def pointer_rgba(data: bytes, kind: int, width: int, height: int, pitch: int) -> Any:
    """A DXGI pointer shape as straight-alpha RGBA ``uint8`` ``(h, w, 4)``.

    Inverting pixels (monochrome AND=1/XOR=1, masked-colour XOR pixels) get
    alpha exactly 1, which the shader draws as "the opposite of below".
    """
    import numpy as np  # noqa: PLC0415

    if width <= 0 or height <= 0:
        return None
    if kind == _POINTER_MONOCHROME:
        rows = height // 2
        bits = np.unpackbits(
            np.frombuffer(data, dtype=np.uint8)[: pitch * height].reshape(height, pitch), axis=1
        )[:, :width]
        and_mask, xor_mask = bits[:rows].astype(bool), bits[rows:].astype(bool)
        out = np.zeros((rows, width, 4), dtype=np.uint8)
        white = ~and_mask & xor_mask
        black = ~and_mask & ~xor_mask
        invert = and_mask & xor_mask
        out[white] = (255, 255, 255, 255)
        out[black] = (0, 0, 0, 255)
        out[invert] = (0, 0, 0, 1)
        return out
    pixels = np.frombuffer(data, dtype=np.uint8)[: pitch * height].reshape(height, pitch)
    bgra = pixels[:, : width * 4].reshape(height, width, 4)
    out = np.empty_like(bgra)
    out[..., 0], out[..., 1], out[..., 2] = bgra[..., 2], bgra[..., 1], bgra[..., 0]
    if kind == _POINTER_MASKED:
        mask = bgra[..., 3] == 0xFF
        rgb_set = bgra[..., :3].any(axis=2)
        out[..., 3] = 255
        out[mask & ~rgb_set, 3] = 0
        out[mask & rgb_set, 3] = 1
        return out
    out[..., 3] = bgra[..., 3]
    out[out[..., 3] == 1, 3] = 0  # 1 is reserved for inverting pixels
    return out
