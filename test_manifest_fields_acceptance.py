# -*- coding: utf-8 -*-
"""Acceptance tests for the version.json one-click-update fields (zip_url /
sha256 / size) in update_checker.parse_manifest, and the unchanged four old
keys. Includes the v3.2.0 tag's parse_manifest verbatim, to prove a 3.2.0
client reads the new version.json without error and with the same result.

Run:  python test_manifest_fields_acceptance.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="floweypet_manifest_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import update_checker as uc  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


# parse_manifest copied verbatim from `git show v3.2.0:src/update_checker.py`
LEGACY_SOURCE = '''
def parse_manifest(raw_bytes):
    """version.json -> {"version", "url", "line", "face"} or None. Never raises."""
    try:
        data = json.loads(raw_bytes)
        if not isinstance(data, dict):
            return None
        ver = data.get("version")
        if parse_version(ver) is None:
            return None
        url = data.get("url")
        if not isinstance(url, str) or not url.startswith(ALLOWED_URL_PREFIX):
            url = DEFAULT_RELEASE_URL
        line = data.get("line")
        if isinstance(line, str) and line.strip():
            line = line.strip()[:MAX_LINE_CHARS]
        else:
            line = None
        face = data.get("face")
        if not (isinstance(face, str) and face):
            face = None
        return {"version": ver, "url": url, "line": line, "face": face}
    except Exception:  # noqa: BLE001 - arbitrary remote bytes must never raise
        return None
'''
_ns = {
    "json": json,
    "parse_version": uc.parse_version,
    "ALLOWED_URL_PREFIX": uc.ALLOWED_URL_PREFIX,
    "DEFAULT_RELEASE_URL": uc.DEFAULT_RELEASE_URL,
    "MAX_LINE_CHARS": uc.MAX_LINE_CHARS,
}
exec(LEGACY_SOURCE, _ns)
legacy_parse = _ns["parse_manifest"]

GOOD_SHA = "9bf3af6aeee8e9aeff6ee3d762eea3eead1b14e5bded52736684368174af1bc4"
GOOD_ZIP = "https://github.com/winwinking/FloweyPet/releases/download/v3.3.0/FloweyPet-v3.3.0.zip"
OLD_KEYS = ("version", "url", "line", "face")
NEW_KEYS = ("zip_url", "sha256", "size")
_MISSING = object()


def manifest(**over):
    d = {
        "version": "3.3.0",
        "url": "https://github.com/winwinking/FloweyPet/releases/tag/v3.3.0",
        "line": "新版本来啦",
        "face": "wink",
        "zip_url": GOOD_ZIP,
        "sha256": GOOD_SHA,
        "size": 40789012,
    }
    d.update(over)
    return json.dumps({k: v for k, v in d.items() if v is not _MISSING}).encode("utf-8")


def all_none(m):
    return m is not None and all(m[k] is None for k in NEW_KEYS)


print("== 1 合法三项 ==")
m = uc.parse_manifest(manifest())
check("返回字典含 7 个键", m is not None and set(m) == set(OLD_KEYS + NEW_KEYS))
check("zip_url 原样", m["zip_url"] == GOOD_ZIP)
check("sha256 原样(已是小写)", m["sha256"] == GOOD_SHA)
check("size 原样", m["size"] == 40789012 and type(m["size"]) is int)
m = uc.parse_manifest(manifest(sha256=GOOD_SHA.upper()))
check("sha256 大写 -> 转小写", m["sha256"] == GOOD_SHA)
m = uc.parse_manifest(manifest(size=200 * 1024 * 1024))
check("size 恰好 200MB 允许", m["size"] == 200 * 1024 * 1024)
m = uc.parse_manifest(manifest(size=1))
check("size=1 允许", m["size"] == 1)

print("== 2 任何一项不合法 -> 三项全 None ==")
bad_cases = {
    "zip_url 缺失": {"zip_url": _MISSING},
    "zip_url 非字符串": {"zip_url": 123},
    "zip_url None": {"zip_url": None},
    "zip_url http": {"zip_url": GOOD_ZIP.replace("https://", "http://")},
    "zip_url 其他主机": {"zip_url": "https://evil.example/winwinking/FloweyPet/releases/download/v1/x.zip"},
    "zip_url 其他仓库": {"zip_url": "https://github.com/someone/FloweyPet/releases/download/v1/x.zip"},
    "zip_url 前缀仅 releases/tag": {"zip_url": "https://github.com/winwinking/FloweyPet/releases/tag/v3.3.0"},
    "zip_url 大小写不同的主机": {"zip_url": GOOD_ZIP.replace("github.com", "GitHub.com")},
    "sha256 缺失": {"sha256": _MISSING},
    "sha256 非字符串": {"sha256": 12345},
    "sha256 63 位": {"sha256": GOOD_SHA[:-1]},
    "sha256 65 位": {"sha256": GOOD_SHA + "0"},
    "sha256 含非十六进制": {"sha256": "g" + GOOD_SHA[1:]},
    "sha256 首部空白": {"sha256": " " + GOOD_SHA},
    "sha256 末尾换行": {"sha256": GOOD_SHA + "\n"},
    "sha256 空串": {"sha256": ""},
    "size 缺失": {"size": _MISSING},
    "size 是 bool True": {"size": True},
    "size 是 bool False": {"size": False},
    "size 是 0": {"size": 0},
    "size 是负数": {"size": -5},
    "size 超 200MB 一字节": {"size": 200 * 1024 * 1024 + 1},
    "size 是浮点": {"size": 1000.5},
    "size 是整数值浮点": {"size": 1000.0},
    "size 是字符串": {"size": "1000"},
    "size None": {"size": None},
}
for label, over in bad_cases.items():
    m = uc.parse_manifest(manifest(**over))
    check(f"{label}: 三项全 None", all_none(m), repr(m))
    check(f"{label}: 旧四键仍正常",
          m is not None and m["version"] == "3.3.0" and m["line"] == "新版本来啦" and m["face"] == "wink")

print("== 3 旧四键输出与改之前一致 ==")
samples = [
    json.dumps({"version": "3.2.0", "url": "https://github.com/winwinking/FloweyPet/releases/tag/v3.2.0",
                "line": "", "face": "neutral"}).encode(),
    json.dumps({"version": "3.2.0"}).encode(),
    json.dumps({"version": "3.2.0", "url": "https://evil.example/x", "line": "  hi  ", "face": ""}).encode(),
    json.dumps({"version": "3.2.0", "line": "x" * 500, "face": 5}).encode(),
    manifest(),
    manifest(zip_url="bad"),
    '{"version":"3.2.0","line":"好"}'.encode("utf-8"),
]
for i, raw in enumerate(samples):
    new = uc.parse_manifest(raw)
    old = legacy_parse(raw)
    check(f"样本{i}: 旧四键与 v3.2.0 完全相同", new is not None and {k: new[k] for k in OLD_KEYS} == old,
          f"{new!r} vs {old!r}")
for label, raw in (("坏 JSON", b"{not json"), ("数组", b"[1,2]"), ("字符串", b'"3.2.0"'), ("无 version", b"{}"),
                   ("version 非法", b'{"version":"v3.3.0"}'), ("空", b""), ("None 字节", None)):
    check(f"{label}: 新旧都返回 None", uc.parse_manifest(raw) is None and legacy_parse(raw) is None)
m = uc.parse_manifest(json.dumps({"version": "3.2.0"}).encode())
check("只有 version: 三项新字段为 None", all_none(m))
check("只有 version: url 回退 / line None / face None",
      m["url"] == uc.DEFAULT_RELEASE_URL and m["line"] is None and m["face"] is None)

print("== 4 v3.2.0 的 parse_manifest 读带新字段的 version.json ==")
raw = manifest()
try:
    old = legacy_parse(raw)
    ok = True
except Exception:  # noqa: BLE001
    old, ok = None, False
check("3.2.0 解析带新字段 version.json 不抛异常", ok)
check("3.2.0 解析结果只含 4 键且值不变",
      old == {"version": "3.3.0", "url": "https://github.com/winwinking/FloweyPet/releases/tag/v3.3.0",
              "line": "新版本来啦", "face": "wink"}, repr(old))
no_fields = json.dumps({"version": "3.3.0", "url": "https://github.com/winwinking/FloweyPet/releases/tag/v3.3.0",
                        "line": "新版本来啦", "face": "wink"}).encode()
check("3.2.0 对「有新字段」与「无新字段」结果完全相同", legacy_parse(raw) == legacy_parse(no_fields))
check("3.2.0 看到的版本比 3.2.0 新", uc.is_newer(old["version"], "3.2.0") is True)

print("== 5 当前仓库 version.json ==")
repo_json = Path(__file__).resolve().parent / "version.json"
try:
    cur = uc.parse_manifest(repo_json.read_bytes())
    check("仓库根 version.json 可解析", cur is not None)
    check("仓库根 version.json 若没有新字段 -> 三项 None(不支持一键更新)",
          cur is not None and (all_none(cur) or all(cur[k] is not None for k in NEW_KEYS)))
except OSError as e:
    check("仓库根 version.json 可读", False, repr(e))

print("== 6 build_release.py 的 draft 含新字段 ==")
src = (Path(__file__).resolve().parent / "tools" / "build_release.py").read_text(encoding="utf-8")
check('build_release 生成 zip_url',
      '"zip_url": "https://github.com/winwinking/FloweyPet/releases/download/v%s/%s" % (version, zip_name)' in src)
check('build_release 生成 "sha256": digest', '"sha256": digest' in src)
check('build_release 生成 "size": os.path.getsize(zip_path)', '"size": os.path.getsize(zip_path)' in src)
built = "https://github.com/winwinking/FloweyPet/releases/download/v%s/%s" % ("3.3.0", "FloweyPet-v3.3.0.zip")
check("build_release 式样的 zip_url 通过 parse_manifest 校验",
      uc.parse_manifest(manifest(zip_url=built))["zip_url"] == built)

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("version.json 新字段用例全部通过")
sys.exit(0)
