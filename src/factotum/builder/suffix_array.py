from array import array
import ctypes
from dataclasses import dataclass
from pathlib import Path
import sys


@dataclass
class NgramResult:
    freq: int
    text: str


def _load_native_lib() -> ctypes.CDLL | None:
    native_dir = Path(__file__).resolve().parent / "native"
    if sys.platform == "win32":
        lib_path = native_dir / "sa_lcp.dll"
    elif sys.platform == "darwin":
        lib_path = native_dir / "sa_lcp.dylib"
    else:
        lib_path = native_dir / "sa_lcp.so"

    if not lib_path.exists():
        return None
    try:
        lib = ctypes.CDLL(str(lib_path))
        i32_p = ctypes.POINTER(ctypes.c_int32)
        lib.build_sa_lcp_native.argtypes = [i32_p, ctypes.c_int32, i32_p, i32_p]
        lib.build_sa_lcp_native.restype = None
        lib.extract_candidates_native.argtypes = [
            i32_p,
            ctypes.c_int32,
            i32_p,
            i32_p,
            i32_p,
        ]
        lib.extract_candidates_native.restype = ctypes.c_int32
        return lib
    except OSError:
        return None


_NATIVE_LIB = _load_native_lib()
USING_NATIVE_CPP: bool = _NATIVE_LIB is not None


def convert_text_to_int(tokens: list[str]) -> list[int]:
    """Ánh xạ mỗi từ duy nhất sang mã số nguyên dương phục vụ xây Suffix Array."""
    lookup: dict[str, int] = {}
    ids: list[int] = []
    next_id = 1
    for s in tokens:
        if s not in lookup:
            lookup[s] = next_id
            next_id += 1
        ids.append(lookup[s])
    return ids


def counting_sort(order: list[int], rank: list[int], classes: int) -> list[int]:
    n = len(order)
    cnt = [0] * classes
    for idx in order:
        cnt[rank[idx]] += 1
    for i in range(1, classes):
        cnt[i] += cnt[i - 1]

    res = [0] * n
    for i in range(n - 1, -1, -1):
        idx = order[i]
        val = rank[idx]
        cnt[val] -= 1
        res[cnt[val]] = idx
    return res


def _build_sa_py(s: list[int]) -> list[int]:
    n = len(s)
    if n == 0:
        return []
    if n == 1:
        return [0]
    alphabet = sorted(set(s))
    rank_map = {val: idx for idx, val in enumerate(alphabet)}
    c = [rank_map[x] for x in s]
    classes = len(alphabet)

    p = counting_sort(list(range(n)), c, classes)

    k = 0
    while (1 << k) < n:
        step = 1 << k
        p_new = list(range(n - step, n)) + [x - step for x in p if x >= step]
        p = counting_sort(p_new, c, classes)
        c_new = [0] * n
        c_new[p[0]] = 0
        classes = 1
        for i in range(1, n):
            curr_first = c[p[i]]
            prev_first = c[p[i - 1]]
            curr_second = c[p[i] + step] if p[i] + step < n else -1
            prev_second = c[p[i - 1] + step] if p[i - 1] + step < n else -1
            if curr_first != prev_first or curr_second != prev_second:
                classes += 1
            c_new[p[i]] = classes - 1
        c = c_new
        if classes == n:
            break
        k += 1
    return p


def _build_lcp_py(s: list[int], p: list[int]) -> list[int]:
    n = len(s)
    rank = [0] * n
    for i in range(n):
        rank[p[i]] = i

    k = 0
    lcp = [0] * n
    for i in range(n):
        if rank[i] == n - 1:
            k = 0
            continue
        j = p[rank[i] + 1]
        while i + k < n and j + k < n and s[i + k] == s[j + k]:
            k += 1
        lcp[rank[i] + 1] = k
        if k > 0:
            k -= 1
    return lcp


def build_sa_lcp(s: list[int]) -> tuple[list[int], list[int]]:
    """Xây đồng thời Suffix Array và LCP Array (ưu tiên lõi C++ `sa_lcp.dll`, tự fallback sang Python)."""
    n = len(s)
    if n == 0:
        return [], []
    if n == 1:
        return [0], [0]

    if _NATIVE_LIB is not None:
        s_arr = array("i", s)
        sa_arr = array("i", [0]) * n
        lcp_arr = array("i", [0]) * n
        i32_p = ctypes.POINTER(ctypes.c_int32)
        _NATIVE_LIB.build_sa_lcp_native(
            ctypes.cast(s_arr.buffer_info()[0], i32_p),
            n,
            ctypes.cast(sa_arr.buffer_info()[0], i32_p),
            ctypes.cast(lcp_arr.buffer_info()[0], i32_p),
        )
        return sa_arr.tolist(), lcp_arr.tolist()

    sa = _build_sa_py(s)
    lcp = _build_lcp_py(s, sa)
    return sa, lcp


def extract_candidates(lcp: list[int], n_with_dummy: int) -> list[tuple[int, int, int]]:
    """Duyệt mảng LCP bằng Monotonic Stack để tìm các ứng viên Maximal Repeats (len, freq, sa_idx)."""
    if n_with_dummy <= 1:
        return []

    if _NATIVE_LIB is not None:
        lcp_arr = array("i", lcp[:n_with_dummy])
        out_len = array("i", [0]) * n_with_dummy
        out_freq = array("i", [0]) * n_with_dummy
        out_pos = array("i", [0]) * n_with_dummy
        i32_p = ctypes.POINTER(ctypes.c_int32)
        count = _NATIVE_LIB.extract_candidates_native(
            ctypes.cast(lcp_arr.buffer_info()[0], i32_p),
            n_with_dummy,
            ctypes.cast(out_len.buffer_info()[0], i32_p),
            ctypes.cast(out_freq.buffer_info()[0], i32_p),
            ctypes.cast(out_pos.buffer_info()[0], i32_p),
        )
        return list(zip(out_len[:count], out_freq[:count], out_pos[:count]))

    candidates: list[tuple[int, int, int]] = []
    stack: list[tuple[int, int]] = []
    for i in range(1, n_with_dummy + 1):
        curr_lcp = lcp[i] if i < n_with_dummy else 0
        start = i
        while stack and stack[-1][0] > curr_lcp:
            prev_len, prev_start = stack.pop()
            freq = i - prev_start + 1
            if prev_len > 0:
                candidates.append((prev_len, freq, prev_start))
            start = prev_start
        if not stack or stack[-1][0] < curr_lcp:
            stack.append((curr_lcp, start))

    candidates.sort(key=lambda x: (-x[1], -x[0]))
    return candidates
