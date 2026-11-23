from pathlib import Path
import re
import struct
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

FOLDER_PATH = Path(__file__).resolve().parent.parent / "data"

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_W_BODY = _W_NS + "body"
_W_P = _W_NS + "p"
_W_T = _W_NS + "t"
_W_TAB = _W_NS + "tab"
_W_BR = _W_NS + "br"
_W_CR = _W_NS + "cr"


def _read_ole2_streams(data: bytes) -> dict[str, bytes]:
    """Đọc các stream chính từ file OLE2 (.doc Word 97-2003) bằng pure Python."""
    if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ValueError("Không phải file OLE2 .doc hợp lệ")

    sector_shift = struct.unpack_from("<H", data, 0x1E)[0]
    sector_size = 1 << sector_shift
    first_dir_sec = struct.unpack_from("<I", data, 0x30)[0]
    first_difat_sec = struct.unpack_from("<I", data, 0x44)[0]

    def read_sector(sec_id: int) -> bytes:
        offset = (sec_id + 1) * sector_size
        return data[offset : offset + sector_size]

    # 1. Thu thập danh sách FAT sector từ DIFAT
    fat_sectors = list(struct.unpack_from("<109I", data, 0x4C))
    difat_sec = first_difat_sec
    while difat_sec not in (0xFFFFFFFE, 0xFFFFFFFF):
        sec_bytes = read_sector(difat_sec)
        count = sector_size // 4 - 1
        fat_sectors.extend(struct.unpack_from(f"<{count}I", sec_bytes, 0))
        difat_sec = struct.unpack_from("<I", sec_bytes, count * 4)[0]

    # 2. Xây bảng FAT
    fat: list[int] = []
    entries_per_sec = sector_size // 4
    for fsec in fat_sectors:
        if fsec in (0xFFFFFFFE, 0xFFFFFFFF):
            continue
        sec_bytes = read_sector(fsec)
        fat.extend(struct.unpack_from(f"<{entries_per_sec}I", sec_bytes, 0))

    def read_chain(start_sec: int, size: int | None = None) -> bytes:
        chunks = []
        sec = start_sec
        visited = set()
        while sec not in (0xFFFFFFFE, 0xFFFFFFFF) and sec < len(fat) and sec not in visited:
            visited.add(sec)
            chunks.append(read_sector(sec))
            sec = fat[sec]
        raw = b"".join(chunks)
        return raw[:size] if size is not None else raw

    # 3. Đọc Directory entries
    dir_data = read_chain(first_dir_sec)
    streams: dict[str, bytes] = {}
    for offset in range(0, len(dir_data), 128):
        entry = dir_data[offset : offset + 128]
        if len(entry) < 128:
            break
        name_len = struct.unpack_from("<H", entry, 64)[0]
        obj_type = entry[66]
        if obj_type != 2 or name_len < 2:  # 2 = Stream
            continue
        name = entry[: name_len - 2].decode("utf-16le", errors="ignore")
        start_sec = struct.unpack_from("<I", entry, 116)[0]
        stream_size = struct.unpack_from("<I", entry, 120)[0]
        if name in ("WordDocument", "0Table", "1Table"):
            streams[name] = read_chain(start_sec, stream_size)

    return streams


def read_doc_file(file_path: Path) -> str:
    """Trích xuất văn bản tiếng Việt từ file .doc (Word 97-2003) theo chuẩn MS-DOC Piece Table."""
    data = file_path.read_bytes()
    streams = _read_ole2_streams(data)
    word_doc = streams.get("WordDocument")
    if not word_doc:
        return ""

    flags = struct.unpack_from("<H", word_doc, 0x0A)[0]
    table_name = "1Table" if (flags & 0x0200) else "0Table"
    table_stream = streams.get(table_name)
    if not table_stream:
        return ""

    fc_clx = struct.unpack_from("<I", word_doc, 0x01A2)[0]
    lcb_clx = struct.unpack_from("<I", word_doc, 0x01A6)[0]
    clx = table_stream[fc_clx : fc_clx + lcb_clx]

    pos = 0
    while pos < len(clx) and clx[pos] == 0x01:
        cb_grpprl = struct.unpack_from("<H", clx, pos + 1)[0]
        pos += 3 + cb_grpprl

    if pos >= len(clx) or clx[pos] != 0x02:
        return ""

    lcb_pcdt = struct.unpack_from("<I", clx, pos + 1)[0]
    pcdt = clx[pos + 5 : pos + 5 + lcb_pcdt]
    n_pieces = (lcb_pcdt - 4) // 12
    cps = struct.unpack_from(f"<{n_pieces + 1}I", pcdt, 0)
    pcd_base = (n_pieces + 1) * 4

    parts: list[str] = []
    for i in range(n_pieces):
        char_count = cps[i + 1] - cps[i]
        fc_raw = struct.unpack_from("<I", pcdt, pcd_base + i * 8 + 2)[0]
        if fc_raw & 0x40000000:  # ANSI (1 byte/char)
            fc = (fc_raw & 0x3FFFFFFF) // 2
            chunk = word_doc[fc : fc + char_count].decode("cp1252", errors="ignore")
        else:  # UTF-16LE (2 bytes/char)
            fc = fc_raw & 0x3FFFFFFF
            chunk = word_doc[fc : fc + char_count * 2].decode("utf-16le", errors="ignore")
        parts.append(chunk)

    text = "".join(parts)
    # Xóa mã field Word (\x13...\x14...\x15) và các ký tự điều khiển bảng
    text = re.sub(r"\x13[^\x14\x15]*\x14", "", text)
    text = text.replace("\x15", "").replace("\x07", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", " ", text)
    return unicodedata.normalize("NFC", text)


def read_docx_file(file_path: Path) -> str:
    """Đọc nhanh văn bản từ file .docx bằng cách giải mã trực tiếp word/document.xml."""
    with zipfile.ZipFile(file_path, "r") as z:
        xml_bytes = z.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    body = root.find(_W_BODY)
    if body is None:
        return ""
    paras: list[str] = []
    for p in body.findall(_W_P):
        parts: list[str] = []
        for elem in p.iter():
            tag = elem.tag
            if tag == _W_T and elem.text:
                parts.append(elem.text)
            elif tag == _W_TAB:
                parts.append("\t")
            elif tag in (_W_BR, _W_CR):
                parts.append("\n")
        paras.append("".join(parts))
    return unicodedata.normalize("NFC", "\n".join(paras))


def read_single_file(file_path: Path) -> str:
    """Đọc 1 file .docx hoặc .doc và chuẩn hóa về NFC."""
    suffix = file_path.suffix.lower()
    if suffix == ".docx":
        return read_docx_file(file_path)
    if suffix == ".doc":
        return read_doc_file(file_path)
    raise ValueError(f"Định dạng chưa hỗ trợ: {file_path.suffix}")


# file bắt đầu bằng ~$ là file tạm của Word khi đang mở
def read_data(folder_path) -> str | None:
    folder = Path(folder_path)
    files = [
        f
        for f in folder.iterdir()
        if f.is_file()
        and f.suffix.lower() in (".docx", ".doc")
        and not f.name.startswith("~$")
    ]
    if not files:
        print("Thư mục trống")
        return None
    latest_file = max(files, key=lambda f: f.stat().st_mtime)
    return read_single_file(latest_file)


def read_all_files(folder_path) -> list[tuple[str, str]]:
    """Đọc tất cả các file .doc / .docx trong thư mục, trả về [(filename, text), ...]."""
    folder = Path(folder_path)
    files = sorted(
        f
        for f in folder.iterdir()
        if f.is_file()
        and f.suffix.lower() in (".docx", ".doc")
        and not f.name.startswith("~$")
    )
    return [(f.name, read_single_file(f)) for f in files]


def main():
    content = read_data(FOLDER_PATH)
    if content:
        print(content[:500])


if __name__ == "__main__":
    main()
