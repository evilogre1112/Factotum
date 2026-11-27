"""
MDIS — Cấu trúc Giao cắt Đa chiều có Thứ tự (Multi-Dimensional Ordered Intersection Structure)
Triển khai cấu trúc vật lý cho lõi đồ thị ngữ cảnh của Factotum:
- `entities`: Thực thể (từ đơn có dấu + thực thể giả AUX 'H' cho chiều âm hứng đầu dư).
- `dims`: Mỗi chiều có `owner`, `mode` ('+' đọc trái->phải, '-' đọc phải->trái), và mảng `event_ids` vật lý.
- `events`: Giao điểm đối ứng hai phía `e -> (dim_left, dim_right)` truy xuất `other_entity(e, d)` trong O(1).
- `edge_postings`: Chỉ mục tọa độ vật lý nén 64-bit `(dim_id << 16) | slot` thay cho quét chuỗi con.
"""

from __future__ import annotations

from array import array
from collections import defaultdict
import json
from pathlib import Path
import zlib

MDIS_MAGIC = b"MDIS"
AUX_ENTITY = "<AUX>"
_SLOT_MASK = 0xFFFF


class MDISStore:
    """Cấu trúc dữ liệu vật lý MDIS cho kho cụm thực thể Factotum."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        # 1. Thực thể (Entities): ID 0 luôn là thực thể giả AUX (<AUX>)
        self.id_to_entity: list[str] = [AUX_ENTITY]
        self.entity_to_id: dict[str, int] = {AUX_ENTITY: 0}
        self.entity_dims: list[list[int]] = [[]]  # entity_id -> list[dim_id]

        # 2. Chiều (Dimensions): chiều '+' (ngữ cảnh xuôi) và chiều '-' (hứng đầu đối ứng / ngữ cảnh ngược)
        self.dim_owner: array = array("I")        # dim_id -> entity_id
        self.dim_mode: bytearray = bytearray()    # dim_id -> 1 ('+') hoặc 0 ('-')
        self.dim_freq: array = array("I")         # dim_id -> tần suất cụm KB (0 với chiều ẩn AUX)
        self.dim_pid: array = array("I")          # dim_id -> pid tương ứng trong GraphIndex.phrases
        self.dim_events: list[array] = []         # dim_id -> array('I') các event_id vật lý (append đuôi)

        # 3. Giao điểm hai phía (Two-sided Intersection Events)
        self.event_left_dim: array = array("I")   # event_id -> dim_id phía trái (chiều '+')
        self.event_right_dim: array = array("I")  # event_id -> dim_id phía phải (chiều '-')

        # Chiều âm hứng đầu đối ứng (d_trash-) trên mỗi thực thể thực + 1 chiều AUX tổng
        self._entity_in_dim: dict[int, int] = {}
        self._aux_dim_id: int = -1
        self._aux_capped: set[int] = set()

        # 4. Chỉ mục tọa độ vật lý nén 64-bit: (eid_u << 32) | eid_v -> array('Q') chứa (dim_id << 16) | slot
        #    slot = 0: eid_u là owner(dim), eid_v là other_entity(dim_events[0], dim)
        #    slot = k >= 1: eid_u là other_entity(dim_events[k-1], dim), eid_v là other_entity(dim_events[k], dim)
        self.edge_postings: dict[int, array] = {}
        self._edge_postings_tmp: dict[int, list[int]] | None = None

    def _get_or_add_entity(self, word: str) -> int:
        eid = self.entity_to_id.get(word)
        if eid is None:
            eid = len(self.id_to_entity)
            self.id_to_entity.append(word)
            self.entity_to_id[word] = eid
            self.entity_dims.append([])
        return eid

    def _allocate_dim(self, owner_eid: int, mode_plus: bool, freq: int = 0, pid: int = 0) -> int:
        did = len(self.dim_owner)
        self.dim_owner.append(owner_eid)
        self.dim_mode.append(1 if mode_plus else 0)
        self.dim_freq.append(freq)
        self.dim_pid.append(pid)
        self.dim_events.append(array("I"))
        self.entity_dims[owner_eid].append(did)
        return did

    def _get_or_add_in_dim(self, target_eid: int) -> int:
        """Lấy hoặc tạo chiều âm d_in- (hứng giao điểm đối ứng) trên thực thể đích."""
        did = self._entity_in_dim.get(target_eid)
        if did is None:
            did = self._allocate_dim(target_eid, mode_plus=False, freq=0, pid=0)
            self._entity_in_dim[target_eid] = did
        return did

    def _append_event(self, dim_left: int, dim_right: int) -> int:
        """Thêm 1 giao điểm đối ứng hai phía bằng append vào cuối mảng vật lý của cả hai chiều."""
        eid = len(self.event_left_dim)
        self.event_left_dim.append(dim_left)
        self.event_right_dim.append(dim_right)
        self.dim_events[dim_left].append(eid)
        self.dim_events[dim_right].append(eid)
        return eid

    def other_entity(self, event_id: int, dim_id: int) -> int:
        """Tìm entity_id đối diện của `event_id` trên chiều `dim_id` trong O(1)."""
        d_l = self.event_left_dim[event_id]
        other_dim = self.event_right_dim[event_id] if d_l == dim_id else d_l
        return self.dim_owner[other_dim]

    def logical_events(self, dim_id: int) -> list[int]:
        """Thứ tự đọc logic: chiều '+' đọc trái->phải; chiều '-' đọc phải->trái."""
        evs = self.dim_events[dim_id]
        return list(evs) if self.dim_mode[dim_id] == 1 else list(reversed(evs))

    def logical_labels(self, dim_id: int) -> list[str]:
        """Trả về chuỗi nhãn thực thể đối diện theo đúng thứ tự đọc logic của chiều `dim_id`."""
        id_to_e = self.id_to_entity
        return [id_to_e[self.other_entity(e, dim_id)] for e in self.logical_events(dim_id)]

    def query_prefix(self, root: str, targets: list[str]) -> bool:
        """Kiểm tra quan hệ MDIS: `targets` có là tiền tố đọc logic của ít nhất một chiều thuộc `root`."""
        if not targets:
            return False
        root_eid = self.entity_to_id.get(root)
        if root_eid is None:
            return False
        target_eids: list[int] = []
        for t in targets:
            teid = self.entity_to_id.get(t)
            if teid is None:
                return False
            target_eids.append(teid)

        t_len = len(target_eids)
        dim_mode = self.dim_mode
        dim_events = self.dim_events
        other_ent = self.other_entity
        for did in self.entity_dims[root_eid]:
            evs = dim_events[did]
            n_ev = len(evs)
            if n_ev < t_len:
                continue
            if dim_mode[did] == 1:
                if all(other_ent(evs[i], did) == target_eids[i] for i in range(t_len)):
                    return True
            else:
                # Chiều '-': đọc ngược từ cuối mảng vật lý (n_ev - 1 - i)
                if all(other_ent(evs[n_ev - 1 - i], did) == target_eids[i] for i in range(t_len)):
                    return True
        return False

    def build_from_phrases(self, phrases: list[tuple[str, tuple[str, ...] | list[str], int]]) -> None:
        """Dựng toàn bộ cấu trúc vật lý MDIS từ danh sách cụm tối đại `phrases`."""
        self.reset()
        id_to_ent = self.id_to_entity
        ent_to_id = self.entity_to_id
        ent_dims = self.entity_dims
        dim_owner = self.dim_owner
        dim_mode = self.dim_mode
        dim_freq = self.dim_freq
        dim_pid = self.dim_pid
        dim_events = self.dim_events
        ev_left = self.event_left_dim
        ev_right = self.event_right_dim
        ent_in_dim = self._entity_in_dim
        edge_tmp: dict[int, list[int]] = {}
        next_ev_id = 0
        next_dim_id = 0
        next_ent_id = len(id_to_ent)

        for pid, (_p_str, p_words, p_freq) in enumerate(phrases):
            n_w = len(p_words)
            if n_w < 2:
                if n_w == 1:
                    w0 = p_words[0]
                    if w0 not in ent_to_id:
                        ent_to_id[w0] = next_ent_id
                        id_to_ent.append(w0)
                        ent_dims.append([])
                        next_ent_id += 1
                continue

            eids: list[int] = []
            for w in p_words:
                eid = ent_to_id.get(w)
                if eid is None:
                    eid = next_ent_id
                    ent_to_id[w] = eid
                    id_to_ent.append(w)
                    ent_dims.append([])
                    next_ent_id += 1
                eids.append(eid)

            root_eid = eids[0]
            d_plus = next_dim_id
            next_dim_id += 1
            dim_owner.append(root_eid)
            dim_mode.append(1)
            dim_freq.append(p_freq)
            dim_pid.append(pid)
            d_plus_evs = array("I")
            dim_events.append(d_plus_evs)
            ent_dims[root_eid].append(d_plus)

            prev_eid = root_eid
            for slot in range(n_w - 1):
                target_eid = eids[slot + 1]
                d_in = ent_in_dim.get(target_eid)
                if d_in is None:
                    d_in = next_dim_id
                    next_dim_id += 1
                    dim_owner.append(target_eid)
                    dim_mode.append(0)
                    dim_freq.append(0)
                    dim_pid.append(0)
                    dim_events.append(array("I"))
                    ent_dims[target_eid].append(d_in)
                    ent_in_dim[target_eid] = d_in

                ev_id = next_ev_id
                next_ev_id += 1
                ev_left.append(d_plus)
                ev_right.append(d_in)
                d_plus_evs.append(ev_id)
                dim_events[d_in].append(ev_id)

                edge_key = (prev_eid << 32) | target_eid
                bucket = edge_tmp.get(edge_key)
                if bucket is None:
                    edge_tmp[edge_key] = [(d_plus << 16) | slot]
                else:
                    bucket.append((d_plus << 16) | slot)
                prev_eid = target_eid

        # Gắn 1 sự kiện AUX <AUX> duy nhất vào cuối vật lý của mỗi chiều âm d_in-
        # để che sạch quan hệ ngược dư theo đúng định lý MDIS (chỉ tốn 1 thực thể AUX cho toàn kho)
        if ent_in_dim:
            aux_dim = next_dim_id
            next_dim_id += 1
            self._aux_dim_id = aux_dim
            dim_owner.append(0)
            dim_mode.append(1)
            dim_freq.append(0)
            dim_pid.append(0)
            aux_evs = array("I")
            dim_events.append(aux_evs)
            ent_dims[0].append(aux_dim)
            aux_capped = self._aux_capped
            for d_in in ent_in_dim.values():
                ev_id = next_ev_id
                next_ev_id += 1
                ev_left.append(aux_dim)
                ev_right.append(d_in)
                aux_evs.append(ev_id)
                dim_events[d_in].append(ev_id)
                aux_capped.add(d_in)

        self.edge_postings = {k: array("Q", vals) for k, vals in edge_tmp.items()}

    def add_phrase(self, pid: int, p_words: tuple[str, ...] | list[str], p_freq: int) -> None:
        """Thêm tăng dần 1 cụm mới vào cấu trúc vật lý MDIS (dùng cho `add_phrases`)."""
        n_w = len(p_words)
        if n_w < 2:
            if n_w == 1:
                self._get_or_add_entity(p_words[0])
            return

        eids = [self._get_or_add_entity(w) for w in p_words]
        d_plus = self._allocate_dim(eids[0], mode_plus=True, freq=p_freq, pid=pid)
        if self._aux_dim_id < 0:
            self._aux_dim_id = self._allocate_dim(0, mode_plus=True, freq=0, pid=0)

        for j in range(1, n_w):
            d_in = self._get_or_add_in_dim(eids[j])
            # Nếu chiều âm d_in đã chặn AUX ở cuối vật lý, tạm nhấc sự kiện AUX ra để giữ AUX luôn ở đầu logic
            if d_in in self._aux_capped and self.dim_events[d_in]:
                aux_ev = self.dim_events[d_in].pop()
                self._append_event(d_plus, d_in)
                self.dim_events[d_in].append(aux_ev)
            else:
                self._append_event(d_plus, d_in)
                self._append_event(self._aux_dim_id, d_in)
                self._aux_capped.add(d_in)

        for slot in range(n_w - 1):
            edge_key = (eids[slot] << 32) | eids[slot + 1]
            arr = self.edge_postings.get(edge_key)
            packed = (d_plus << 16) | slot
            if arr is None:
                self.edge_postings[edge_key] = array("Q", (packed,))
            else:
                arr.append(packed)

    def lookup_phrase_score(
        self,
        words: list[str],
        phrases: list[tuple[str, tuple[str, ...], int]],
        max_diff_k: int,
    ) -> tuple[int, int, str]:
        """
        Tra cứu chuỗi liên tiếp `words` trực tiếp trên tọa độ vật lý MDIS (không quét chuỗi con, không giao set từ đơn):
        - Chọn cạnh (w_i, w_{i+1}) có danh sách posting tọa độ ngắn nhất.
        - Xác minh trực tiếp các token còn lại qua `other_entity(dim_events[d][pos], d)` bằng số nguyên O(1).
        """
        n_w = len(words)
        if n_w < 2:
            return 0, -1, ""

        ent_to_id = self.entity_to_id
        eids: list[int] = []
        for w in words:
            eid = ent_to_id.get(w)
            if eid is None:
                return 0, -1, ""
            eids.append(eid)

        edge_postings = self.edge_postings
        best_pair_idx = -1
        best_postings: array | None = None
        best_len = 1 << 30
        for i in range(n_w - 1):
            arr = edge_postings.get((eids[i] << 32) | eids[i + 1])
            if arr is None:
                return 0, -1, ""
            a_len = len(arr)
            if a_len < best_len:
                best_len = a_len
                best_postings = arr
                best_pair_idx = i

        if best_postings is None:
            return 0, -1, ""

        dim_owner = self.dim_owner
        dim_events = self.dim_events
        dim_freq = self.dim_freq
        dim_pid = self.dim_pid
        ev_right = self.event_right_dim
        best_freq = 0
        best_diff = max_diff_k + 1
        best_pid = -1

        for packed in best_postings:
            did = packed >> 16
            pair_slot = packed & _SLOT_MASK
            start_slot = pair_slot - best_pair_idx
            if start_slot < 0:
                continue
            evs = dim_events[did]
            dim_len_words = len(evs) + 1
            diff = dim_len_words - n_w
            if diff < 0 or diff > max_diff_k or start_slot + n_w > dim_len_words:
                continue
            if diff > best_diff:
                continue

            # Xác minh các vị trí khác best_pair_idx và best_pair_idx + 1 bằng tọa độ vật lý trên chiều did
            matched = True
            for k in range(n_w):
                if k == best_pair_idx or k == best_pair_idx + 1:
                    continue
                pos = start_slot + k
                ent_at_pos = dim_owner[did] if pos == 0 else dim_owner[ev_right[evs[pos - 1]]]
                if ent_at_pos != eids[k]:
                    matched = False
                    break
            if not matched:
                continue

            f_d = dim_freq[did]
            if diff < best_diff or (diff == best_diff and f_d > best_freq):
                best_diff = diff
                best_freq = f_d
                best_pid = dim_pid[did]

        if best_freq > 0 and best_pid >= 0:
            return best_freq, best_diff, phrases[best_pid][0]
        return 0, -1, ""

    def stats(self) -> dict[str, int]:
        """Thống kê cấu trúc vật lý MDIS (số thực thể, số chiều +/-, số giao điểm hai phía, số posting 64-bit)."""
        n_plus = sum(1 for m in self.dim_mode if m == 1)
        n_minus = len(self.dim_mode) - n_plus
        total_postings = sum(len(arr) for arr in self.edge_postings.values())
        return {
            "entities": len(self.id_to_entity) - 1,  # không tính <AUX>
            "aux_entities": 1 if self._aux_dim_id >= 0 else 0,
            "dims_plus": n_plus,
            "dims_minus": n_minus,
            "events": len(self.event_left_dim),
            "edge_keys": len(self.edge_postings),
            "edge_postings": total_postings,
        }

    def save_file(self, path: str | Path) -> Path:
        """
        Xuất cấu trúc vật lý MDIS ra file tri thức nén `.mdis` (JSON + zlib):
        - `entities`: danh sách thực thể theo ID
        - `dims`: [owner_eid, mode_int, freq, pid, event_ids]
        - `events`: [left_dim_list, right_dim_list]
        """
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": "MDIS-v1",
            "entities": self.id_to_entity,
            "dims": [
                [
                    int(self.dim_owner[d]),
                    int(self.dim_mode[d]),
                    int(self.dim_freq[d]),
                    int(self.dim_pid[d]),
                    list(self.dim_events[d]),
                ]
                for d in range(len(self.dim_owner))
            ],
            "events_left": list(self.event_left_dim),
            "events_right": list(self.event_right_dim),
        }
        raw = MDIS_MAGIC + zlib.compress(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            level=3,
        )
        dest.write_bytes(raw)
        return dest

    def load_file(self, path: str | Path) -> None:
        """Nạp cấu trúc vật lý MDIS từ file tri thức `.mdis` và dựng chỉ mục tọa độ 64-bit."""
        src = Path(path)
        raw = src.read_bytes()
        if not raw.startswith(MDIS_MAGIC):
            raise ValueError(f"File '{src.name}' không đúng định dạng MDIS.")
        payload = json.loads(zlib.decompress(raw[4:]).decode("utf-8"))
        self.reset()
        self.id_to_entity = payload["entities"]
        self.entity_to_id = {e: i for i, e in enumerate(self.id_to_entity)}
        self.entity_dims = [[] for _ in self.id_to_entity]
        self.event_left_dim = array("I", payload["events_left"])
        self.event_right_dim = array("I", payload["events_right"])

        for did, (owner_eid, mode_int, freq, pid, evs) in enumerate(payload["dims"]):
            self.dim_owner.append(owner_eid)
            self.dim_mode.append(mode_int)
            self.dim_freq.append(freq)
            self.dim_pid.append(pid)
            self.dim_events.append(array("I", evs))
            self.entity_dims[owner_eid].append(did)
            if owner_eid == 0 and mode_int == 1:
                self._aux_dim_id = did
            elif mode_int == 0:
                self._entity_in_dim[owner_eid] = did
                if evs:
                    self._aux_capped.add(did)

        # Dựng lại chỉ mục tọa độ vật lý 64-bit từ các chiều '+' thực
        edge_tmp: dict[int, list[int]] = defaultdict(list)
        ev_right = self.event_right_dim
        dim_owner = self.dim_owner
        for did in range(len(self.dim_owner)):
            if self.dim_mode[did] != 1 or dim_owner[did] == 0:
                continue
            evs = self.dim_events[did]
            if not evs:
                continue
            prev_eid = dim_owner[did]
            for slot, ev_id in enumerate(evs):
                cur_eid = dim_owner[ev_right[ev_id]]
                edge_tmp[(prev_eid << 32) | cur_eid].append((did << 16) | slot)
                prev_eid = cur_eid
        self.edge_postings = {k: array("Q", vals) for k, vals in edge_tmp.items()}

    def export_text(self, path: str | Path) -> Path:
        """
        Xuất toàn bộ cấu trúc vật lý MDIS ra file văn bản thuần UTF-8 (`.txt`) để đọc/kiểm tra trực tiếp:
        - `[ENTITIES]`: danh sách thực thể theo ID (`E0 <AUX>`, `E1 ...`)
        - `[DIMENSIONS_PLUS]`: các chiều dương `+` theo thứ tự logic trái->phải (`e<id>:<entity> -> ...`)
        - `[DIMENSIONS_MINUS]`: các chiều âm `-` theo thứ tự logic phải->trái (bắt đầu bằng `<AUX>`)
        """
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        st = self.stats()
        id_to_ent = self.id_to_entity
        dim_owner = self.dim_owner
        dim_mode = self.dim_mode
        dim_freq = self.dim_freq
        dim_pid = self.dim_pid
        dim_events = self.dim_events
        ev_left = self.event_left_dim
        ev_right = self.event_right_dim

        with dest.open("w", encoding="utf-8", newline="\n") as f:
            f.write("# MDIS — Multi-Dimensional Ordered Intersection Structure (Factotum)\n")
            f.write(
                f"# Entities: {st['entities']} (+{st['aux_entities']} <AUX>) | "
                f"Dims(+): {st['dims_plus']} | Dims(-): {st['dims_minus']} | "
                f"Twin Events: {st['events']} | Edge Keys: {st['edge_keys']}\n\n"
            )

            f.write("[ENTITIES]\n")
            for eid, name in enumerate(id_to_ent):
                f.write(f"E{eid}\t{name}\n")

            f.write("\n[DIMENSIONS_PLUS]\n")
            f.write("# D<id>\t+<owner>\tfreq=<f>\tpid=<p>\t|\te<ev>:<target> -> ...\n")
            for did in range(len(dim_owner)):
                if dim_mode[did] != 1:
                    continue
                owner = id_to_ent[dim_owner[did]]
                evs = dim_events[did]
                chain = " -> ".join(f"e{ev}:{id_to_ent[dim_owner[ev_right[ev]]]}" for ev in evs)
                f.write(f"D{did}\t+{owner}\tfreq={dim_freq[did]}\tpid={dim_pid[did]}\t|\t{chain}\n")

            f.write("\n[DIMENSIONS_MINUS]\n")
            f.write("# D<id>\t-<owner>\tdeg=<k>\t|\t(logical order right->left; capped by <AUX>)\n")
            for did in range(len(dim_owner)):
                if dim_mode[did] != 0:
                    continue
                owner = id_to_ent[dim_owner[did]]
                evs = dim_events[did]
                # Chiều '-' đọc ngược từ cuối mảng vật lý về đầu
                chain = " <- ".join(
                    f"e{ev}:{id_to_ent[dim_owner[ev_left[ev]]]}" for ev in reversed(evs)
                )
                f.write(f"D{did}\t-{owner}\tdeg={len(evs)}\t|\t{chain}\n")

        return dest
