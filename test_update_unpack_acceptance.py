# -*- coding: utf-8 -*-
"""Acceptance tests for src/update_unpack.py (safe unpacking of the update
zip). Malicious archives are built on the spot with zipfile. If
release/FloweyPet-v3.3.0.zip exists locally (it is git-ignored), it is also
unpacked for real; otherwise that one check is skipped and says so.

Run:  python test_update_unpack_acceptance.py
"""
import os
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import update_unpack as uu  # noqa: E402

warnings.simplefilter("ignore")  # zipfile warns about duplicate names, which we build on purpose

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


TMP = Path(tempfile.mkdtemp(prefix="floweypet_unpack_"))
_counter = [0]
VER = "3.3.0"
TOP = "FloweyPet-v3.3.0"


def build(entries, name=None):
    """entries: list of (arcname, bytes | ZipInfo-attrs tuple). An entry is
    (name, data) or (name, data, external_attr) or (name, data, external_attr, flag_bits)."""
    _counter[0] += 1
    path = TMP / (name or f"z{_counter[0]}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for e in entries:
            arc, data = e[0], e[1]
            zi = zipfile.ZipInfo("placeholder")
            zi.filename = arc  # assign after construction: the ctor would rewrite '\' on Windows
            zi.orig_filename = arc
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = e[2] if len(e) > 2 else (0o100644 << 16)
            z.writestr(zi, data)
    flagged = [(e[0], e[3]) for e in entries if len(e) > 3]
    if flagged:  # writestr resets flag_bits, so patch the central directory by hand
        raw = bytearray(path.read_bytes())
        pos = raw.find(b"PK")
        while pos != -1:
            nlen = int.from_bytes(raw[pos + 28:pos + 30], "little")
            fname = bytes(raw[pos + 46:pos + 46 + nlen]).decode("utf-8", "replace")
            for arc, bits in flagged:
                if fname == arc:
                    cur = int.from_bytes(raw[pos + 8:pos + 10], "little")
                    raw[pos + 8:pos + 10] = (cur | bits).to_bytes(2, "little")
            pos = raw.find(b"PK", pos + 4)
        path.write_bytes(bytes(raw))
    return path


def good_entries(extra=()):
    return [(f"{TOP}/FloweyPet.exe", b"MZ-exe"), (f"{TOP}/dialogue.json", b"{}"),
            (f"{TOP}/assets/a.png", b"png")] + list(extra)


def run(zp, version=VER, dest=None):
    dest = dest or (TMP / f"out{_counter[0]}_{len(os.listdir(TMP))}")
    try:
        return None, uu.unpack(zp, dest, version), dest
    except uu.UpdateUnpackError as e:
        return e, None, dest


def rejected(label, entries, code, version=VER):
    zp = build(entries)
    err, res, dest = run(zp, version)
    check(f"{label} -> {code}", err is not None and err.code == code, repr(err))
    check(f"{label}: 目标已清理(不存在或为空)", not dest.exists() or not any(dest.iterdir()))
    return err


# ------------------------------------------------------------------ 1
print("== 1 正常包 ==")
zp = build(good_entries(), "good.zip")
err, res, dest = run(zp)
check("正常包通过", err is None, repr(err))
check("返回顶层文件夹路径", res == dest / TOP and res.is_dir(), repr(res))
check("FloweyPet.exe 在顶层文件夹里", (res / "FloweyPet.exe").read_bytes() == b"MZ-exe")
check("子目录文件解出", (res / "assets" / "a.png").read_bytes() == b"png")
check("返回值是 Path", isinstance(res, Path))
dest2 = TMP / "empty_dest"
dest2.mkdir()
err, res, _ = run(zp, dest=dest2)
check("dest 是已存在的空文件夹也行", err is None and (res / "FloweyPet.exe").exists(), repr(err))
err, res, dest3 = run(build(good_entries([(f"{TOP}/sub/", b""), (f"{TOP}/sub/x.txt", b"x")])))
check("带目录条目的包通过", err is None and (res / "sub" / "x.txt").read_bytes() == b"x", repr(err))
err, res, _ = run(build(good_entries()), version="3.3.0")
check("版本号 3.3.0 与顶层名匹配", err is None)

# ------------------------------------------------------------------ 2
print("== 2 dest 目录要求 ==")
d = TMP / "nonempty"
d.mkdir()
(d / "keep.txt").write_text("keep")
err, _, _ = run(build(good_entries()), dest=d)
check("dest 非空 -> dest_not_empty", err is not None and err.code == "dest_not_empty", repr(err))
check("dest 非空: 原有文件原样保留", (d / "keep.txt").read_text() == "keep" and len(list(d.iterdir())) == 1)
f = TMP / "afile"
f.write_text("x")
err, _, _ = run(build(good_entries()), dest=f)
check("dest 是普通文件 -> dest_not_empty", err is not None and err.code == "dest_not_empty", repr(err))
check("dest 是普通文件: 文件未被删", f.read_text() == "x")
d2 = TMP / "preexisting_empty"
d2.mkdir()
rejected_zip = build(good_entries([(f"{TOP}/../evil.txt", b"x")]))
err, _, _ = run(rejected_zip, dest=d2)
check("失败后预先存在的空 dest 保留为空文件夹", err is not None and d2.is_dir() and not any(d2.iterdir()), repr(err))

# ------------------------------------------------------------------ 3
print("== 3 恶意路径 ==")
rejected("'..' 路径穿越", good_entries([(f"{TOP}/../evil.txt", b"x")]), "bad_path")
rejected("开头 '..'", good_entries([("../evil.txt", b"x")]), "bad_path")
rejected("中间 '..'", good_entries([(f"{TOP}/a/../../evil.txt", b"x")]), "bad_path")
rejected("绝对路径 /etc", good_entries([("/etc/passwd", b"x")]), "bad_path")
rejected("绝对路径 /FloweyPet-v3.3.0/...", [("/" + TOP + "/FloweyPet.exe", b"x")], "bad_path")
rejected("盘符 C:", good_entries([("C:/Windows/evil.dll", b"x")]), "bad_path")
rejected("盘符 无斜杠 C:evil", good_entries([("C:evil.dll", b"x")]), "bad_path")
rejected("NTFS 数据流 a:b", good_entries([(f"{TOP}/a.txt:stream", b"x")]), "bad_path")
rejected("反斜杠 路径穿越", good_entries([(f"{TOP}\\..\\..\\evil.txt", b"x")]), "bad_path")
rejected("反斜杠 普通", good_entries([(f"{TOP}\\sub\\x.txt", b"x")]), "bad_path")
rejected("UNC 路径", good_entries([("\\\\server\\share\\x", b"x")]), "bad_path")
rejected("'.' 段", good_entries([(f"{TOP}/./x.txt", b"x")]), "bad_path")
rejected("空段 //", good_entries([(f"{TOP}//x.txt", b"x")]), "bad_path")
rejected("NUL 控制字符", good_entries([(f"{TOP}/a\x00b.txt", b"x")]), "bad_path")
rejected("文件名以点结尾", good_entries([(f"{TOP}/evil.exe.", b"x")]), "bad_path")
rejected("文件名以空格结尾", good_entries([(f"{TOP}/evil.exe ", b"x")]), "bad_path")
rejected("保留设备名 NUL", good_entries([(f"{TOP}/nul", b"x")]), "bad_path")
rejected("保留设备名 com1.txt", good_entries([(f"{TOP}/com1.txt", b"x")]), "bad_path")
rejected("空文件名", good_entries([("", b"x")]), "bad_path")

# ------------------------------------------------------------------ 4
print("== 4 符号链接 / 特殊文件 / 加密 / 重复 ==")
rejected("符号链接", good_entries([(f"{TOP}/link", b"/etc/passwd", 0o120777 << 16)]), "symlink")
rejected("符号链接指向 exe", good_entries([(f"{TOP}/link.exe", b"FloweyPet.exe", 0o120755 << 16)]), "symlink")
rejected("设备文件", good_entries([(f"{TOP}/dev", b"", 0o060666 << 16)]), "symlink")
rejected("加密条目", good_entries([(f"{TOP}/secret.bin", b"x", 0o100644 << 16, 0x1)]), "encrypted")
rejected("重名 (完全相同)", good_entries([(f"{TOP}/dialogue.json", b"dup")]), "duplicate")
rejected("重名 (大小写不同)", good_entries([(f"{TOP}/DIALOGUE.JSON", b"dup")]), "duplicate")
rejected("重名 (目录和文件同名)", good_entries([(f"{TOP}/dialogue.json/", b"")]), "duplicate")
rejected("重名 (顶层 exe 大小写)", good_entries([(f"{TOP}/floweypet.EXE", b"dup")]), "duplicate")

# ------------------------------------------------------------------ 5
print("== 5 数量与大小限制 ==")
check("默认限制: 2000 条", uu.MAX_ENTRIES == 2000)
check("默认限制: 500MB", uu.MAX_TOTAL_BYTES == 500 * 1024 * 1024)
many = good_entries([(f"{TOP}/f/{i}.txt", b"") for i in range(2000 - 3 + 1)])
check("构造的包恰好 2001 条", len(many) == 2001)
rejected("2001 条 -> too_many_entries", many, "too_many_entries")
exactly = good_entries([(f"{TOP}/f/{i}.txt", b"") for i in range(2000 - 3)])
check("构造的包恰好 2000 条", len(exactly) == 2000)
err, res, dest = run(build(exactly))
check("恰好 2000 条允许", err is None, repr(err))
big_zero = b"\0" * (50 * 1024 * 1024)
saved = uu.MAX_TOTAL_BYTES
try:
    uu.MAX_TOTAL_BYTES = 60 * 1024 * 1024  # test-only: avoid writing 500MB
    rejected("声明总大小超限 -> too_large", good_entries([(f"{TOP}/big1.bin", big_zero), (f"{TOP}/big2.bin", big_zero)]), "too_large")
    err, res, dest = run(build(good_entries([(f"{TOP}/big1.bin", big_zero)])))
    check("总大小未超限时允许", err is None, repr(err))
finally:
    uu.MAX_TOTAL_BYTES = saved
check("还原后默认限制仍是 500MB", uu.MAX_TOTAL_BYTES == 500 * 1024 * 1024)
# 压缩炸弹: 高压缩比的大文件按"解压后大小"计
bomb_zip = build(good_entries([(f"{TOP}/bomb.bin", b"\0" * (120 * 1024 * 1024))]), "bomb.zip")
check("炸弹包压缩后很小", bomb_zip.stat().st_size < 1024 * 1024)
try:
    uu.MAX_TOTAL_BYTES = 100 * 1024 * 1024
    err, res, dest = run(bomb_zip)
    check("高压缩比炸弹按解压大小拒绝 -> too_large", err is not None and err.code == "too_large", repr(err))
    check("炸弹: 无残留", not dest.exists() or not any(dest.iterdir()))
finally:
    uu.MAX_TOTAL_BYTES = saved

# ------------------------------------------------------------------ 6
print("== 6 顶层结构 ==")
rejected("两个顶层文件夹", good_entries([("Other/x.txt", b"x")]), "bad_layout")
rejected("顶层有散落文件", good_entries([("README.txt", b"x")]), "bad_layout")
rejected("没有顶层文件夹(文件直接在根)", [("FloweyPet.exe", b"x"), ("a.dll", b"x")], "bad_layout")
rejected("空包", [], "bad_layout")
rejected("顶层名字版本不对", [(f"FloweyPet-v3.2.0/FloweyPet.exe", b"x")], "wrong_version")
rejected("顶层名字前缀不对", [("FloweyPet-3.3.0/FloweyPet.exe", b"x")], "wrong_version")
rejected("顶层名字多了后缀", [(f"{TOP}-beta/FloweyPet.exe", b"x")], "wrong_version")
rejected("顶层名字大小写不同", [(f"floweypet-v3.3.0/FloweyPet.exe", b"x")], "wrong_version")
rejected("缺 FloweyPet.exe", [(f"{TOP}/dialogue.json", b"{}"), (f"{TOP}/a.dll", b"x")], "missing_exe")
rejected("exe 在子目录而不是直接在顶层", [(f"{TOP}/bin/FloweyPet.exe", b"x"), (f"{TOP}/a.dll", b"x")], "missing_exe")
rejected("exe 名字大小写不同", [(f"{TOP}/floweypet.exe", b"x")], "missing_exe")
rejected("FloweyPet.exe 是目录", [(f"{TOP}/FloweyPet.exe/", b""), (f"{TOP}/a.dll", b"x")], "missing_exe")
err, _, _ = run(build(good_entries()), version="3.3.1")
check("expected_version 与包不符 -> wrong_version", err is not None and err.code == "wrong_version", repr(err))
for bad in ("v3.3.0", "3.3.0 ", "", "3..3", "3.3.x", "..", "3.3.0/..", None, 330):
    try:
        uu.unpack(zp, TMP / "badver", bad)
        got = None
    except ValueError:
        got = "ValueError"
    except Exception as ex:  # noqa: BLE001
        got = type(ex).__name__
    check(f"非法 expected_version {bad!r} -> ValueError", got == "ValueError", got)
check("非法 expected_version 没有创建目录", not (TMP / "badver").exists())

# ------------------------------------------------------------------ 7
print("== 7 损坏的包 / 解压中途失败 ==")
junk = TMP / "junk.zip"
junk.write_bytes(b"this is not a zip")
err, _, dest = run(junk)
check("不是 zip -> bad_zip", err is not None and err.code == "bad_zip", repr(err))
check("bad_zip: 没有创建目标", not dest.exists())
err, _, dest = run(TMP / "does_not_exist.zip")
check("zip 不存在 -> bad_zip", err is not None and err.code == "bad_zip", repr(err))
good = (TMP / "good.zip").read_bytes()
trunc = TMP / "trunc.zip"
trunc.write_bytes(good[:len(good) // 2])
err, _, dest = run(trunc)
check("截断的 zip -> bad_zip", err is not None and err.code == "bad_zip", repr(err))
# CRC 损坏: 改一个压缩数据字节
raw = bytearray(build([(f"{TOP}/FloweyPet.exe", os.urandom(2000)), (f"{TOP}/z.bin", os.urandom(2000))], "crc.zip").read_bytes())
raw[60] ^= 0xFF
(TMP / "crc_bad.zip").write_bytes(bytes(raw))
err, _, dest = run(TMP / "crc_bad.zip")
check("数据损坏 -> 拒绝且无残留", err is not None and err.code in ("bad_zip", "extract_failed") and (not dest.exists() or not any(dest.iterdir())), repr(err))
# 中途失败: 文件与目录冲突, 解压到一半才发现
zconf = build([(f"{TOP}/FloweyPet.exe", b"exe"), (f"{TOP}/a", b"i am a file"), (f"{TOP}/a/b.txt", b"under a file"),
               (f"{TOP}/z.txt", b"z")])
err, _, dest = run(zconf)
check("解压中途失败 -> extract_failed", err is not None and err.code == "extract_failed", repr(err))
check("中途失败: 已解出的东西被删掉", not dest.exists() or not any(dest.iterdir()))
d3 = TMP / "preexisting_empty2"
d3.mkdir()
err, _, _ = run(zconf, dest=d3)
check("中途失败 + 预先存在的空 dest: 清空但保留文件夹", err is not None and d3.is_dir() and not any(d3.iterdir()), repr(err))

# ------------------------------------------------------------------ 8
print("== 8 路径逃逸的最后防线 ==")
# 校验层之外再检查真实路径: 让 _split_name 失效, 路径检查仍应挡住
orig_split = uu._split_name
orig_vet = uu._vet
evil = build([(f"{TOP}/FloweyPet.exe", b"exe"), (f"{TOP}/../../escape.txt", b"x")])
try:
    uu._vet = lambda infos, v: TOP  # pretend vetting passed
    uu._split_name = lambda raw: (raw.split("/"), False)
    err, res, dest = run(evil)
    check("预校验被绕过时, 真实路径检查仍拒绝逃逸", err is not None and err.code == "bad_path", repr(err))
    check("逃逸文件没有被写出", not (dest.parent / "escape.txt").exists() and not (TMP.parent / "escape.txt").exists())
    check("最后防线失败后目标已清理", not dest.exists() or not any(dest.iterdir()))
finally:
    uu._split_name = orig_split
    uu._vet = orig_vet

# ------------------------------------------------------------------ 9
print("== 9 其他 ==")
src = (Path(__file__).resolve().parent / "src" / "update_unpack.py").read_text(encoding="utf-8")
import re  # noqa: E402

imports = set(re.findall(r"^(?:import|from)\s+([A-Za-z_][\w]*)", src, re.M))
check("模块只导入标准库", imports <= {"os", "re", "shutil", "stat", "zipfile", "zlib", "pathlib"}, repr(imports))
check("模块没有 extractall / print / logging", "extractall" not in src.replace("extractall() is never used", "")
      and "print(" not in src and "logging" not in src)
codes = {uu.BAD_ZIP, uu.DEST_NOT_EMPTY, uu.BAD_PATH, uu.SYMLINK, uu.DUPLICATE, uu.ENCRYPTED, uu.TOO_MANY_ENTRIES,
         uu.TOO_LARGE, uu.BAD_LAYOUT, uu.WRONG_VERSION, uu.MISSING_EXE, uu.EXTRACT_FAILED}
check("原因代码 12 个互不相同", len(codes) == 12)
e = uu.UpdateUnpackError("bad_path", "x")
check("异常带 code/detail", e.code == "bad_path" and e.detail == "x" and str(e).startswith("bad_path"))

# ------------------------------------------------------------------ 10
print("== 10 真实发布包 ==")
real = Path(__file__).resolve().parent / "release" / "FloweyPet-v3.3.0.zip"
if not real.exists():
    print(f"[SKIP] {real} 不存在, 跳过真实解压检查 (release/ 被 git 忽略, 需要先运行 tools/build_release.py)")
else:
    with zipfile.ZipFile(real) as z:
        listed = [i for i in z.infolist() if not i.filename.endswith("/")]
    err, res, dest = run(real)
    check("真实发布包解压通过", err is None, repr(err))
    if err is None:
        files = [p for p in res.rglob("*") if p.is_file()]
        check("文件数与 zip 一致", len(files) == len(listed), f"{len(files)} vs {len(listed)}")
        check("FloweyPet.exe 大小与 zip 记录一致",
              (res / "FloweyPet.exe").stat().st_size == next(i.file_size for i in listed if i.filename == f"{TOP}/FloweyPet.exe"))
        check("所有文件大小都与 zip 记录一致",
              all((res / i.filename[len(TOP) + 1:]).stat().st_size == i.file_size for i in listed))
        check("所有解出的文件真实路径都在 dest 里",
              all(os.path.realpath(p).startswith(os.path.realpath(dest)) for p in files))
        check("包含 dialogue.json 与 assets 目录", (res / "dialogue.json").is_file() and (res / "assets").is_dir())

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("安全解压用例全部通过")
sys.exit(0)
