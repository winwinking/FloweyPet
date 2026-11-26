# -*- coding: utf-8 -*-
"""Safe unpacking of the downloaded update zip.

Standard library only: no Qt, no config, no paths, no app_log, and nothing
here logs. extractall() is never used: every entry is vetted first, then
written one at a time with exclusive-create into a destination that must be
new or empty. On any failure everything already written is removed and an
UpdateUnpackError carrying a reason code is raised.

Entry rules: no absolute paths, drive letters / ':' , '..', backslashes,
symlinks (or any non-regular file), duplicate names (case-insensitive),
encrypted entries, Windows-reserved device names, or names ending in '.' /
' '. At most MAX_ENTRIES entries and MAX_TOTAL_BYTES unpacked. Exactly one
top-level folder named "FloweyPet-v<expected_version>", containing
FloweyPet.exe directly.
"""
import os
import re
import shutil
import stat
import zipfile
import zlib
from pathlib import Path

MAX_ENTRIES = 2000
MAX_TOTAL_BYTES = 500 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
TOP_PREFIX = "FloweyPet-v"
EXE_NAME = "FloweyPet.exe"

# Reason codes carried by UpdateUnpackError.code
BAD_ZIP = "bad_zip"
DEST_NOT_EMPTY = "dest_not_empty"
BAD_PATH = "bad_path"
SYMLINK = "symlink"
DUPLICATE = "duplicate"
ENCRYPTED = "encrypted"
TOO_MANY_ENTRIES = "too_many_entries"
TOO_LARGE = "too_large"
BAD_LAYOUT = "bad_layout"
WRONG_VERSION = "wrong_version"
MISSING_EXE = "missing_exe"
EXTRACT_FAILED = "extract_failed"

_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)*")
_RESERVED_NAMES = frozenset(
    ["con", "prn", "aux", "nul"]
    + ["com%d" % i for i in range(1, 10)]
    + ["lpt%d" % i for i in range(1, 10)]
)


class UpdateUnpackError(Exception):
    """Any unpack failure. `code` is one of the reason codes above."""

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _show(name):
    return repr(name if len(name) <= 80 else name[:80] + "...")


def _split_name(raw):
    """Vets one raw entry name and returns (parts, is_dir)."""
    if not isinstance(raw, str) or not raw:
        raise UpdateUnpackError(BAD_PATH, "empty entry name")
    if "\\" in raw:
        raise UpdateUnpackError(BAD_PATH, f"backslash in {_show(raw)}")
    if ":" in raw:
        raise UpdateUnpackError(BAD_PATH, f"drive letter or ':' in {_show(raw)}")
    if raw.startswith("/"):
        raise UpdateUnpackError(BAD_PATH, f"absolute path {_show(raw)}")
    if any(ord(c) < 32 for c in raw):
        raise UpdateUnpackError(BAD_PATH, f"control character in {_show(raw)}")
    is_dir = raw.endswith("/")
    parts = raw[:-1].split("/") if is_dir else raw.split("/")
    for part in parts:
        if part == "":
            raise UpdateUnpackError(BAD_PATH, f"empty path segment in {_show(raw)}")
        if part in (".", ".."):
            raise UpdateUnpackError(BAD_PATH, f"'{part}' segment in {_show(raw)}")
        if part.endswith((".", " ")):
            raise UpdateUnpackError(BAD_PATH, f"segment ends with '.' or space in {_show(raw)}")
        if part.split(".", 1)[0].lower() in _RESERVED_NAMES:
            raise UpdateUnpackError(BAD_PATH, f"reserved device name in {_show(raw)}")
    return parts, is_dir


def _vet(infos, expected_version):
    """Checks every entry before anything is written. Returns the top folder
    name."""
    if len(infos) > MAX_ENTRIES:
        raise UpdateUnpackError(TOO_MANY_ENTRIES, f"{len(infos)} entries (max {MAX_ENTRIES})")
    if not infos:
        raise UpdateUnpackError(BAD_LAYOUT, "empty archive")

    seen = set()
    tops = set()
    total = 0
    exe_found = False
    top_expected = TOP_PREFIX + expected_version
    for info in infos:
        # orig_filename is the name as stored; on Windows ZipInfo.filename has
        # already had '\' turned into '/', which would hide backslash tricks.
        raw = info.orig_filename
        parts, is_dir = _split_name(raw)

        mode = (info.external_attr >> 16) & 0xFFFF
        if mode:
            if stat.S_ISLNK(mode):
                raise UpdateUnpackError(SYMLINK, f"symlink entry {_show(raw)}")
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise UpdateUnpackError(SYMLINK, f"special file entry {_show(raw)}")
        if info.flag_bits & 0x1:
            raise UpdateUnpackError(ENCRYPTED, f"encrypted entry {_show(raw)}")

        key = "/".join(parts).casefold()
        if key in seen:
            raise UpdateUnpackError(DUPLICATE, f"duplicate entry {_show(raw)}")
        seen.add(key)

        if len(parts) == 1 and not is_dir:
            raise UpdateUnpackError(BAD_LAYOUT, f"file outside the top folder: {_show(raw)}")
        tops.add(parts[0])
        if not is_dir:
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise UpdateUnpackError(TOO_LARGE, f"unpacked size exceeds {MAX_TOTAL_BYTES} bytes")
            if len(parts) == 2 and parts[1] == EXE_NAME and parts[0] == top_expected:
                exe_found = True

    if len(tops) != 1:
        raise UpdateUnpackError(BAD_LAYOUT, f"{len(tops)} top-level entries, expected exactly one folder")
    top = next(iter(tops))
    if top != top_expected:
        raise UpdateUnpackError(WRONG_VERSION, f"top folder is {_show(top)}, expected {top_expected!r}")
    if not exe_found:
        raise UpdateUnpackError(MISSING_EXE, f"{EXE_NAME} not found directly inside {top_expected!r}")
    return top


def _is_inside(root, path):
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:  # different drives
        return False


def _clear(dest, created):
    if created:
        shutil.rmtree(dest, ignore_errors=True)
        return
    try:
        for child in os.listdir(dest):
            p = os.path.join(dest, child)
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    os.remove(p)
                except OSError:
                    pass
    except OSError:
        pass


def unpack(zip_path, dest_dir, expected_version):
    """Unpacks `zip_path` into `dest_dir` and returns the Path of the
    top-level folder inside it ("<dest_dir>/FloweyPet-v<expected_version>").
    dest_dir must not exist yet, or be an empty folder."""
    if not (isinstance(expected_version, str) and _VERSION_RE.fullmatch(expected_version)):
        raise ValueError("expected_version must be dotted digits like '3.3.0'")
    dest = os.path.abspath(os.fspath(dest_dir))

    if os.path.lexists(dest):
        if os.path.islink(dest) or not os.path.isdir(dest) or os.listdir(dest):
            raise UpdateUnpackError(DEST_NOT_EMPTY, "destination exists and is not an empty folder")
        created = False
    else:
        created = True

    try:
        zf = zipfile.ZipFile(os.fspath(zip_path))
    except (zipfile.BadZipFile, OSError, NotImplementedError):
        raise UpdateUnpackError(BAD_ZIP, "cannot open the archive") from None

    try:
        with zf:
            infos = zf.infolist()
            top = _vet(infos, expected_version)

            try:
                os.makedirs(dest, exist_ok=True)
            except OSError:
                raise UpdateUnpackError(EXTRACT_FAILED, "cannot create the destination") from None
            dest_real = os.path.realpath(dest)
            written = 0
            try:
                for info in infos:
                    parts, is_dir = _split_name(info.orig_filename)
                    target = os.path.join(dest_real, *parts)
                    if not _is_inside(dest_real, os.path.realpath(target)):
                        raise UpdateUnpackError(BAD_PATH, f"escapes the destination: {_show(info.orig_filename)}")
                    if is_dir:
                        os.makedirs(target, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    if not _is_inside(dest_real, os.path.realpath(os.path.dirname(target))):
                        raise UpdateUnpackError(BAD_PATH, f"escapes the destination: {_show(info.orig_filename)}")
                    with zf.open(info) as src, open(target, "xb") as out:
                        while True:
                            chunk = src.read(CHUNK_BYTES)
                            if not chunk:
                                break
                            written += len(chunk)
                            if written > MAX_TOTAL_BYTES:
                                raise UpdateUnpackError(TOO_LARGE, f"unpacked size exceeds {MAX_TOTAL_BYTES} bytes")
                            out.write(chunk)
            except UpdateUnpackError:
                raise
            except (zipfile.BadZipFile, zlib.error):
                raise UpdateUnpackError(BAD_ZIP, "corrupt archive data") from None
            except (OSError, RuntimeError, NotImplementedError, EOFError, zipfile.LargeZipFile):
                raise UpdateUnpackError(EXTRACT_FAILED, "could not write an entry") from None
    except BaseException:
        _clear(dest, created)
        raise

    result = Path(dest) / top
    if not (result / EXE_NAME).is_file():
        _clear(dest, created)
        raise UpdateUnpackError(MISSING_EXE, f"{EXE_NAME} missing after unpacking")
    return result
