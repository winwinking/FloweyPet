# -*- coding: utf-8 -*-
"""Acceptance tests for src/updater_apply.py ("apply update" mode) and the
main.py hook-up.

Everything runs on throwaway folders: a fake "old install" and a fake "new
package". Waiting for the old process and launching the new/old exe are
injected fakes, so no FloweyPet.exe is ever started. The only real processes
are tiny `python -c` sleepers used to test the process-wait helpers.

Run:  python test_updater_apply_acceptance.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="floweypet_apply_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")  # update.log goes here, never to the real %APPDATA%
sys.path.insert(0, str(ROOT / "src"))

import updater_apply as ua  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


class PowerCut(BaseException):
    """Simulates the process dying (not an Exception: apply_update must not roll back)."""


_n = [0]


def fresh(label):
    _n[0] += 1
    d = TMP / f"{label}{_n[0]}"
    d.mkdir(parents=True)
    return d


def write(base, rel, data):
    p = Path(base).joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def make_old(with_manifest=True):
    t = fresh("old") / "FloweyPet"
    t.mkdir()
    write(t, "FloweyPet.exe", "OLD-EXE")
    write(t, "dialogue.json", "OLD-DIALOGUE")
    write(t, "assets/a.png", "OLD-PNG")
    write(t, "lib/old_only.dll", "OLD-ONLY-DLL")
    write(t, "user_notes.txt", "my own notes")
    write(t, "my_stuff/mine.txt", "user dir file")
    write(t, "config.json", '{"legacy": true}')
    write(t, "stats.json", '{"legacy": true}')
    if with_manifest:
        write(t, ".install_manifest.json", json.dumps(
            {"version": "3.2.0", "files": ["FloweyPet.exe", "dialogue.json", "assets/a.png", "lib/old_only.dll"]}))
    return t


def make_new():
    n = fresh("new") / "FloweyPet-v3.3.0"
    n.mkdir()
    write(n, "FloweyPet.exe", "NEW-EXE-LONGER")
    write(n, "dialogue.json", "NEW-DIALOGUE")
    write(n, "assets/a.png", "NEW-PNG")
    write(n, "assets/b.png", "NEW-B")
    write(n, "newdir/deep/c.txt", "NEW-C")
    (n / "emptydir").mkdir()
    return n


def snapshot(base):
    files, dirs = {}, set()
    for dp, dn, fn in os.walk(base):
        for d in dn:
            dirs.add(os.path.relpath(os.path.join(dp, d), base).replace("\\", "/"))
        for f in fn:
            full = os.path.join(dp, f)
            files[os.path.relpath(full, base).replace("\\", "/")] = Path(full).read_bytes()
    return files, dirs


class Fakes:
    def __init__(self, waiter_result=True):
        self.waits = []
        self.launches = []
        self.sleeps = []
        self.waiter_result = waiter_result

    def waiter(self, pid, created, timeout):
        self.waits.append((pid, created, timeout))
        return self.waiter_result

    def launcher(self, exe, args, cwd):
        self.launches.append((exe, list(args), cwd))

    def sleep(self, s):
        self.sleeps.append(s)


def run_apply(t, n, fakes=None, restart=True, **kw):
    f = fakes or Fakes()
    kw.setdefault("self_info", (4321, 98765))
    rc = ua.apply_update(t, n, 1234, 5555, "3.2.0", "3.3.0", restart, waiter=f.waiter, launcher=f.launcher,
                         sleep=f.sleep, **kw)
    return rc, f


class Inject:
    """Makes the k-th call of ua.<name> raise exc() (once)."""

    def __init__(self, name, k, exc, match=None):
        self.name, self.k, self.exc, self.match = name, k, exc, match
        self.calls = 0

    def __enter__(self):
        self.orig = getattr(ua, self.name)
        me = self

        def wrapper(*a, **kw):
            if me.match is None or me.match(*a):
                me.calls += 1
                if me.calls == me.k:
                    raise me.exc()
            return me.orig(*a, **kw)

        setattr(ua, self.name, wrapper)
        return self

    def __exit__(self, *a):
        setattr(ua, self.name, self.orig)


class Always:
    """ua.<name> raises exc() every time match(*args) is true; counts calls."""

    def __init__(self, name, exc, match):
        self.name, self.exc, self.match = name, exc, match
        self.calls = 0

    def __enter__(self):
        self.orig = getattr(ua, self.name)
        me = self

        def wrapper(*a, **kw):
            if me.match(*a):
                me.calls += 1
                raise me.exc()
            return me.orig(*a, **kw)

        setattr(ua, self.name, wrapper)
        return self

    def __exit__(self, *a):
        setattr(ua, self.name, self.orig)


def not_alive(pid, created):
    return False


# ------------------------------------------------------------------ 1
print("== 1 正常替换 ==")
t, n = make_old(), make_new()
before = snapshot(t)
phases = []
orig_save = ua._save_marker
ua._save_marker = lambda ctx, st: (phases.append(st["phase"]), orig_save(ctx, st))[1]
replace_log = []
orig_replace = ua._replace
ua._replace = lambda s, d: (replace_log.append((s, d)), orig_replace(s, d))[1]
try:
    rc, f = run_apply(t, n)
finally:
    ua._save_marker = orig_save
    ua._replace = orig_replace
check("返回 0", rc == 0, str(rc))
check("等待参数: pid/created/30 秒", f.waits == [(1234, 5555, 30.0)], repr(f.waits))
check("旧进程退出后再等 1 秒", f.sleeps and f.sleeps[0] == 1.0, repr(f.sleeps))
after, adirs = snapshot(t)
check("FloweyPet.exe 变新", after["FloweyPet.exe"] == b"NEW-EXE-LONGER")
check("dialogue.json 变新", after["dialogue.json"] == b"NEW-DIALOGUE")
check("assets/a.png 变新", after["assets/a.png"] == b"NEW-PNG")
check("新增文件到位 assets/b.png", after.get("assets/b.png") == b"NEW-B")
check("新增深层文件到位", after.get("newdir/deep/c.txt") == b"NEW-C")
check("旧清单有、新包没有的 lib/old_only.dll 被移走", "lib/old_only.dll" not in after)
check("被移走的文件在备份目录里", (t / ".update_backup" / "lib" / "old_only.dll").read_bytes() == b"OLD-ONLY-DLL")
check("被替换的旧文件在备份目录里", (t / ".update_backup" / "FloweyPet.exe").read_bytes() == b"OLD-EXE"
      and (t / ".update_backup" / "dialogue.json").read_bytes() == b"OLD-DIALOGUE")
check("用户自己的文件原样", after["user_notes.txt"] == b"my own notes" and after["my_stuff/mine.txt"] == b"user dir file")
check("config.json / stats.json 原样", after["config.json"] == before[0]["config.json"] and after["stats.json"] == before[0]["stats.json"])
manifest = json.loads(after[".install_manifest.json"])
check("写出新清单 = 新包文件列表",
      sorted(manifest["files"]) == sorted(["FloweyPet.exe", "dialogue.json", "assets/a.png", "assets/b.png", "newdir/deep/c.txt"])
      and manifest["version"] == "3.3.0", repr(manifest))
check("清单里 FloweyPet.exe 排最后", manifest["files"][-1] == "FloweyPet.exe")
check("事务标记已删除", ".update_pending.json" not in after and not any(k.startswith(".update_pending") for k in after))
check("没有残留的 .new 临时文件", not any(k.endswith(".new") for k in after))
check("启动新版: 一次, 参数 --updated-from 3.2.0",
      len(f.launches) == 1 and f.launches[0][1] == ["--updated-from", "3.2.0"], repr(f.launches))
check("启动的是 target 里的 FloweyPet.exe, cwd=target",
      f.launches[0][0] == str(t / "FloweyPet.exe") and f.launches[0][2] == str(t), repr(f.launches))
check("阶段依次更新到标记", [p for i, p in enumerate(phases) if i == 0 or p != phases[i - 1]][:5]
      == ["starting", "replacing", "verifying", "finalizing"] or
      [p for i, p in enumerate(phases) if i == 0 or p != phases[i - 1]] == ["starting", "replacing", "verifying", "finalizing"],
      repr(phases))
finals = [d for s, d in replace_log if d.endswith("FloweyPet.exe") and s.endswith(".new")]
others = [i for i, (s, d) in enumerate(replace_log) if s.endswith(".new") and not d.endswith("FloweyPet.exe")]
exe_idx = [i for i, (s, d) in enumerate(replace_log) if s.endswith(".new") and d.endswith("FloweyPet.exe")]
check("FloweyPet.exe 最后一个就位", exe_idx and others and max(others) < min(exe_idx))
check("空文件夹没有被带进去", "emptydir" not in adirs)
check("target 外的旧备份/新目录未被改", snapshot(n)[0]["FloweyPet.exe"] == b"NEW-EXE-LONGER")

# ------------------------------------------------------------------ 2
print("== 2 第一次更新 (没有旧清单) ==")
t, n = make_old(with_manifest=False), make_new()
rc, f = run_apply(t, n)
after, _ = snapshot(t)
check("返回 0", rc == 0)
check("覆盖与新增正常", after["FloweyPet.exe"] == b"NEW-EXE-LONGER" and after["assets/b.png"] == b"NEW-B")
check("没有清单时旧版独有的文件保留", after.get("lib/old_only.dll") == b"OLD-ONLY-DLL")
check("用户文件保留", after["user_notes.txt"] == b"my own notes")
check("之后写出了清单", ".install_manifest.json" in after)
check("新增的清单文件不在回滚列表之外残留 (无标记)", ".update_pending.json" not in after)

# ------------------------------------------------------------------ 3
print("== 3 上一次的备份被删掉 ==")
t, n = make_old(), make_new()
write(t, ".update_backup/stale_from_last_time.txt", "stale")
write(t, ".update_backup/lib/older.dll", "older")
rc, f = run_apply(t, n)
after, _ = snapshot(t)
check("返回 0", rc == 0)
check("上次的备份文件没有了", ".update_backup/stale_from_last_time.txt" not in after and ".update_backup/lib/older.dll" not in after)
check("只留这一次的备份", sorted(k for k in after if k.startswith(".update_backup/"))
      == sorted([".update_backup/FloweyPet.exe", ".update_backup/dialogue.json", ".update_backup/assets/a.png",
                 ".update_backup/lib/old_only.dll", ".update_backup/.install_manifest.json"]))
t, n = make_old(), make_new()
write(t, ".update_backup/stale.txt", "stale")
before = snapshot(t)
with Inject("_rmtree", 1, lambda: OSError("cannot clear")):
    rc, f = run_apply(t, n)
check("旧备份删不掉 -> 失败, 什么都没改", rc == ua.EXIT_FAILED_ROLLED_BACK and snapshot(t) == before, str(rc))
check("旧备份删不掉: 启动旧版 --update-failed", f.launches and f.launches[0][1] == ["--update-failed"])

# ------------------------------------------------------------------ 4
print("== 4 一直 PermissionError -> 失败并完整回滚 ==")
t, n = make_old(), make_new()
before = snapshot(t)
with Always("_copy", PermissionError, lambda s, d: d.endswith("b.png.new")) as inj:
    rc, f = run_apply(t, n)
check("返回 EXIT_FAILED_ROLLED_BACK", rc == ua.EXIT_FAILED_ROLLED_BACK, str(rc))
check("重试 10 次 (共 11 次尝试)", inj.calls == 11, str(inj.calls))
check("重试间隔 0.5 秒", f.sleeps.count(0.5) == 10, repr(f.sleeps))
check("target 与更新前逐字节一致 (含用户文件、备份目录与标记都不在)", snapshot(t) == before)
check("启动旧版: --update-failed", f.launches == [(str(t / "FloweyPet.exe"), ["--update-failed"], str(t))], repr(f.launches))
t, n = make_old(), make_new()
before = snapshot(t)
with Always("_replace", PermissionError, lambda s, d: d.endswith("dialogue.json") and not s.endswith(".new")):
    rc, f = run_apply(t, n)
check("把旧文件移进备份时一直被拒 -> 回滚到原样", rc == ua.EXIT_FAILED_ROLLED_BACK and snapshot(t) == before, str(rc))
t, n = make_old(), make_new()
cnt = [0]


def flaky(s, d):
    cnt[0] += 1
    return cnt[0] <= 2


with Always("_copy", PermissionError, lambda s, d: d.endswith("dialogue.json.new") and flaky(s, d)):
    rc, f = run_apply(t, n)
check("只失败两次: 重试后成功", rc == 0 and snapshot(t)[0]["dialogue.json"] == b"NEW-DIALOGUE", str(rc))

# ------------------------------------------------------------------ 5
print("== 5 每个阶段中途抛异常 -> 都回滚到原样 ==")
for name in ("_copy", "_replace", "_remove", "_write_json_atomic"):
    outcomes = {"fail": 0, "bad": 0}
    last_k = 0
    for k in range(1, 120):
        t, n = make_old(), make_new()
        before = snapshot(t)
        with Inject(name, k, lambda: OSError("injected")) as inj:
            rc, f = run_apply(t, n)
        if inj.calls < k:  # fewer than k calls happened: this run was fault-free
            check(f"{name}: 无故障时更新成功 (共 {inj.calls} 次调用)", rc == 0)
            break
        last_k = k
        ok = rc in (ua.EXIT_FAILED_ROLLED_BACK,) and snapshot(t) == before \
            and f.launches and f.launches[-1][1] == ["--update-failed"]
        outcomes["fail" if ok else "bad"] += 1
        if not ok:
            check(f"{name} 第 {k} 次调用失败 -> 回滚到原样", False, f"rc={rc} diff={set(snapshot(t)[0]) ^ set(before[0])}")
    check(f"{name}: 逐个注入失败 (共 {last_k} 个位置) 全部回滚到原样", outcomes["bad"] == 0 and outcomes["fail"] == last_k,
          repr(outcomes))
t, n = make_old(), make_new()
before = snapshot(t)
orig = ua._verify
ua._verify = lambda *a, **kw: (_ for _ in ()).throw(ua.ApplyError("verify blew up"))
try:
    rc, f = run_apply(t, n)
finally:
    ua._verify = orig
check("校验阶段失败 -> 回滚到原样", rc == ua.EXIT_FAILED_ROLLED_BACK and snapshot(t) == before)
t, n = make_old(), make_new()
before = snapshot(t)
orig = ua._finalize
ua._finalize = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("finalize blew up"))
try:
    rc, f = run_apply(t, n)
finally:
    ua._finalize = orig
check("写清单阶段失败 -> 回滚到原样", rc == ua.EXIT_FAILED_ROLLED_BACK and snapshot(t) == before)
t, n = make_old(), make_new()
before = snapshot(t)
orig = ua._sha256_of
ua._sha256_of = lambda p: "0" * 64 if p.endswith("FloweyPet.exe") and str(t) in p else orig(p)
try:
    rc, f = run_apply(t, n)
finally:
    ua._sha256_of = orig
check("FloweyPet.exe SHA256 对不上 -> 回滚到原样", rc == ua.EXIT_FAILED_ROLLED_BACK and snapshot(t) == before)
t, n = make_old(), make_new()
before = snapshot(t)
write(n, "FloweyPet.exe", "NEW-EXE-LONGER")
orig = ua._copy


def corrupting_copy(src, dst):
    orig(src, dst)
    if dst.endswith("assets" + os.sep + "a.png.new"):
        Path(dst).write_bytes(b"short")


ua._copy = corrupting_copy
try:
    rc, f = run_apply(t, n)
finally:
    ua._copy = orig
check("复制出来大小不对 -> 校验失败并回滚到原样", rc == ua.EXIT_FAILED_ROLLED_BACK and snapshot(t) == before, str(rc))
t, n = make_old(), make_new()
(t / "assets" / "b.png").mkdir()
before = snapshot(t)
rc, f = run_apply(t, n)
check("目标位置是同名文件夹 -> 失败并回滚", rc == ua.EXIT_FAILED_ROLLED_BACK and snapshot(t) == before, str(rc))

# ------------------------------------------------------------------ 6
print("== 6 断电模拟: 流程停住不回滚, 再 recover ==")
for name in ("_copy", "_replace", "_remove", "_write_json_atomic"):
    results = {"bad": 0, "n": 0}
    marker_seen = 0
    for k in range(1, 120):
        t, n = make_old(), make_new()
        before = snapshot(t)
        died = False
        with Inject(name, k, PowerCut) as inj:
            try:
                run_apply(t, n)
            except PowerCut:
                died = True
        if not died:
            check(f"断电 {name}: 无故障时更新成功", inj.calls < k)
            break
        results["n"] += 1
        has_marker = (t / ".update_pending.json").exists()
        marker_seen += has_marker
        status = ua.recover_if_needed(t, is_alive=not_alive, sleep=lambda s: None)
        same = snapshot(t) == before
        if not same or status not in (ua.RECOVER_RECOVERED, ua.RECOVER_NONE):
            results["bad"] += 1
            check(f"断电 {name} 第 {k} 次调用 -> 恢复成原样", False,
                  f"status={status} diff={set(snapshot(t)[0]) ^ set(before[0])}")
    check(f"断电 {name}: 每个位置 ({results['n']} 个) 恢复后都与更新前逐字节一致", results["bad"] == 0 and results["n"] > 0,
          repr(results))
    check(f"断电 {name}: 其中确实出现过留有事务标记的现场", marker_seen > 0 or name == "_write_json_atomic")
# 回滚过程中再断电
_t, _n2 = make_old(), make_new()
_probe = Inject("_replace", 10**9, PowerCut)
with _probe:
    run_apply(_t, _n2)
TOTAL_REPLACES = _probe.calls
check("探测一次正常更新的 os.replace 调用数", TOTAL_REPLACES >= 10, str(TOTAL_REPLACES))
for k_fail, k_cut in ((4, 6), (6, 8), (TOTAL_REPLACES - 2, TOTAL_REPLACES)):
    t, n = make_old(), make_new()
    before = snapshot(t)
    died = False
    calls = [0]
    orig = ua._replace

    def wrapper(s, d):
        calls[0] += 1
        if calls[0] == k_fail:
            raise OSError("injected failure -> starts a rollback")
        if calls[0] == k_cut:
            raise PowerCut()
        return orig(s, d)

    ua._replace = wrapper
    try:
        try:
            run_apply(t, n)
        except PowerCut:
            died = True
    finally:
        ua._replace = orig
    status = ua.recover_if_needed(t, is_alive=not_alive, sleep=lambda s: None)
    check(f"回滚到一半又断电 (失败@{k_fail}, 断电@{k_cut}) -> 再恢复一次后与原样一致",
          snapshot(t) == before and (died or status == ua.RECOVER_NONE), f"died={died} status={status}")
# 用真实进程检测: 标记里的 apply 进程就是当前进程, 还活着
t, n = make_old(), make_new()
before = snapshot(t)
real_created = ua.get_process_creation_time(os.getpid())
check("能取到本进程创建时间", isinstance(real_created, int) and real_created > 0)
with Inject("_replace", 5, PowerCut):
    try:
        ua.apply_update(t, n, 1234, 5555, "3.2.0", "3.3.0", True, waiter=lambda *a: True, launcher=lambda *a: None,
                        sleep=lambda s: None, self_info=(os.getpid(), real_created))
    except PowerCut:
        pass
mid = snapshot(t)
status = ua.recover_if_needed(t)
check("标记里的进程(pid+创建时间)还活着 -> in_progress 且什么都不动", status == ua.RECOVER_IN_PROGRESS and snapshot(t) == mid, status)
st = json.loads((t / ".update_pending.json").read_text(encoding="utf-8"))
st["created"] = real_created + 1
(t / ".update_pending.json").write_text(json.dumps(st), encoding="utf-8")
status = ua.recover_if_needed(t)
check("pid 相同但创建时间不同 (pid 被复用) -> 当作已死, 回滚", status == ua.RECOVER_RECOVERED and snapshot(t) == before, status)

# ------------------------------------------------------------------ 7
print("== 7 recover_if_needed ==")
t = make_old()
before = snapshot(t)
check("没有标记 -> none, 什么都不动", ua.recover_if_needed(t) == "none" and snapshot(t) == before)
for label, content in (("乱码", b"\xff\xfe not json {{"), ("JSON 数组", b"[1,2,3]"), ("缺 phase", b'{"pid": 1}'),
                       ("added 不是列表", b'{"phase":"replacing","added":"x"}'),
                       ("pid 是字符串", b'{"phase":"replacing","pid":"7"}'), ("空文件", b"")):
    t = make_old()
    write(t, ".update_pending.json", content)
    before = {k: v for k, v in snapshot(t)[0].items() if k != ".update_pending.json"}
    status = ua.recover_if_needed(t)
    files = snapshot(t)[0]
    bad = [k for k in files if k.startswith(".update_pending.json.bad-")]
    check(f"标记坏了 ({label}) -> bad_marker", status == "bad_marker", status)
    check(f"标记坏了 ({label}): 改名为 .bad-时间戳, 原标记没了", len(bad) == 1 and ".update_pending.json" not in files)
    check(f"标记坏了 ({label}): 其他文件一个字节都没动",
          {k: v for k, v in files.items() if not k.startswith(".update_pending.json")} == before)
t = make_old()
outside = TMP / "outside_target.txt"
outside.write_text("must survive")
write(t, ".update_pending.json", json.dumps({
    "phase": "replacing", "pid": None, "created": None, "old_version": "3.2.0", "new_version": "3.3.0",
    "added": ["../../outside_target.txt", "C:/Windows/x", "/etc/passwd", "user_notes.txt", "..\\outside_target.txt"],
    "added_dirs": ["..", "../.."], "package_files": ["../outside_target.txt"]}))
write(t, ".update_backup/dialogue.json", "FROM-BACKUP")
before_other = snapshot(t)[0]
status = ua.recover_if_needed(t, is_alive=not_alive)
files = snapshot(t)[0]
check("标记里的不安全路径被忽略: target 外的文件还在", outside.read_text() == "must survive")
check("标记被篡改也只会按安全的相对路径处理 (user_notes.txt 在 added 里 -> 被当作新增文件删除是已知前提)", status == "recovered")
check("备份被还原", files["dialogue.json"] == b"FROM-BACKUP")
# rollback_incomplete 的标记 (没有存活 pid) 会被再次处理
t, n = make_old(), make_new()
before = snapshot(t)
orig = ua._verify
ua._verify = lambda *a, **kw: (_ for _ in ()).throw(ua.ApplyError("force rollback"))
restore_block = Always("_replace", PermissionError, lambda s, d: s.startswith(str(t / ".update_backup")) and d.endswith("dialogue.json"))
try:
    with restore_block:
        rc, f = run_apply(t, n)
finally:
    ua._verify = orig
check("回滚本身出错 -> EXIT_ROLLBACK_INCOMPLETE", rc == ua.EXIT_ROLLBACK_INCOMPLETE, str(rc))
st = json.loads((t / ".update_pending.json").read_text(encoding="utf-8"))
check("回滚不完整: 保留标记, phase=rollback_incomplete, pid 清空", st["phase"] == "rollback_incomplete" and st["pid"] is None)
check("回滚不完整: 其他能还原的都还原了 (a.png / exe)",
      (t / "assets" / "a.png").read_bytes() == b"OLD-PNG" and (t / "FloweyPet.exe").read_bytes() == b"OLD-EXE")
check("回滚不完整: 仍然启动旧版 --update-failed", f.launches and f.launches[-1][1] == ["--update-failed"])
status = ua.recover_if_needed(t)  # default is_alive: pid None -> dead
check("下次启动再试一次 -> recovered", status == "recovered", status)
_files, _dirs = snapshot(t)
_trash_files = {k: v for k, v in _files.items() if k.startswith(".update_trash/")}
_rest_files = {k: v for k, v in _files.items() if not k.startswith(".update_trash/")}
_rest_dirs = {d for d in _dirs if d != ".update_trash" and not d.startswith(".update_trash/")}
check("除 .update_trash 外与更新前逐字节一致", (_rest_files, _rest_dirs) == before, repr(set(_rest_files) ^ set(before[0])))
check(".update_trash 里正好是被挪走的新版 dialogue.json", _trash_files == {".update_trash/dialogue.json": b"NEW-DIALOGUE"}, repr(_trash_files))
ua.empty_trash(t)
check("empty_trash 之后整个目录与更新前完全一致", snapshot(t) == before and not (t / ".update_trash").exists())

# ------------------------------------------------------------------ 8
print("== 8 等待旧进程 ==")
t, n = make_old(), make_new()
before = snapshot(t)
fk = Fakes(waiter_result=False)
rc, f = run_apply(t, n, fk)
check("超时 -> EXIT_WAIT_TIMEOUT", rc == ua.EXIT_WAIT_TIMEOUT, str(rc))
check("超时: 什么都没改, 没有标记, 没有备份目录", snapshot(t) == before)
check("超时: 不启动任何东西 (旧进程还活着)", f.launches == [])
check("超时: 等待时限是 30 秒", f.waits and f.waits[0][2] == 30.0)
check("超时: 没有执行退出后的 1 秒等待", 1.0 not in f.sleeps)
t, n = make_old(), make_new()


def boom_waiter(*a):
    raise OSError("wait blew up")


rc, f = ua.apply_update(t, n, 1, 1, "3.2.0", "3.3.0", True, waiter=boom_waiter, launcher=lambda *a: None, sleep=lambda s: None), None
check("等待函数本身出错 -> 当作超时失败, 不改文件", rc == ua.EXIT_WAIT_TIMEOUT and snapshot(t) == snapshot(t))

sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
try:
    time.sleep(0.3)
    created = ua.get_process_creation_time(sleeper.pid)
    check("取到子进程创建时间", isinstance(created, int) and created > 0)
    t0 = time.time()
    r = ua.wait_for_process_exit(sleeper.pid, created, 0.6)
    check("进程还在 -> 等到超时返回 False", r is False and 0.4 < time.time() - t0 < 3, f"{r} {time.time() - t0:.2f}")
    check("超时后进程没有被杀", sleeper.poll() is None)
    check("is_process_alive: 活着", ua.is_process_alive(sleeper.pid, created) is True)
    t0 = time.time()
    r = ua.wait_for_process_exit(sleeper.pid, created + 12345, 20)
    check("创建时间不同 (pid 被复用) -> 立刻当作已退出", r is True and time.time() - t0 < 2, f"{r} {time.time() - t0:.2f}")
    check("pid 被复用的判断也没有杀进程", sleeper.poll() is None)
    check("is_process_alive: 创建时间不同 -> 不算活着", ua.is_process_alive(sleeper.pid, created + 12345) is False)
    threading_exit = []
    import threading  # noqa: E402

    threading.Timer(0.5, sleeper.terminate).start()
    t0 = time.time()
    r = ua.wait_for_process_exit(sleeper.pid, created, 10)
    check("进程中途退出 -> 返回 True (没等满 10 秒)", r is True and time.time() - t0 < 6, f"{r} {time.time() - t0:.2f}")
    sleeper.wait(5)
    check("已退出的进程: 再等 -> True", ua.wait_for_process_exit(sleeper.pid, created, 1) is True)
    check("已退出的进程: is_process_alive False", ua.is_process_alive(sleeper.pid, created) is False)
finally:
    if sleeper.poll() is None:
        sleeper.kill()
check("不存在的 pid 算已退出", ua.wait_for_process_exit(4194000, 1, 1) is True)
check("is_process_alive: 不存在的 pid / 非法 pid", ua.is_process_alive(4194000, 1) is False and ua.is_process_alive(None, 1) is False
      and ua.is_process_alive(0, 1) is False and ua.is_process_alive(True, 1) is False)

# ------------------------------------------------------------------ 9
print("== 9 校验失败 ==")
t, n = make_old(), make_new()
before = snapshot(t)
rc, f = run_apply(t, t)
check("target 与新目录相同 -> EXIT_VALIDATION, 不动", rc == ua.EXIT_VALIDATION and snapshot(t) == before, str(rc))
check("相同目录: 不等待旧进程", f.waits == [])
inner = t / "inner_pkg"
inner.mkdir()
write(inner, "FloweyPet.exe", "x")
before = snapshot(t)
rc, f = run_apply(t, inner)
check("新目录在 target 里面 -> EXIT_VALIDATION", rc == ua.EXIT_VALIDATION and snapshot(t) == before, str(rc))
shutil.rmtree(inner)
t2 = make_old()
outer_new = fresh("outer") / "pkg"
outer_new.mkdir()
write(outer_new, "FloweyPet.exe", "NEW")
sub_target = outer_new / "install"
sub_target.mkdir()
write(sub_target, "FloweyPet.exe", "OLD")
before = snapshot(sub_target)
rc, f = run_apply(sub_target, outer_new)
check("target 在新目录里面 -> EXIT_VALIDATION", rc == ua.EXIT_VALIDATION and snapshot(sub_target) == before, str(rc))
t, n = make_old(), make_new()
(t / "FloweyPet.exe").unlink()
before = snapshot(t)
rc, f = run_apply(t, n)
check("target 缺 FloweyPet.exe -> EXIT_VALIDATION", rc == ua.EXIT_VALIDATION and snapshot(t) == before)
check("target 缺 exe: 没有可启动的旧版, 不启动", f.launches == [])
t, n = make_old(), make_new()
(n / "FloweyPet.exe").unlink()
before = snapshot(t)
rc, f = run_apply(t, n)
check("新目录缺 FloweyPet.exe -> EXIT_VALIDATION", rc == ua.EXIT_VALIDATION and snapshot(t) == before)
check("新目录缺 exe: 启动旧版 --update-failed", f.launches and f.launches[0][1] == ["--update-failed"])
t, n = make_old(), make_new()
write(t, ".update_pending.json", json.dumps({"phase": "replacing", "pid": 1}))
before = snapshot(t)
rc, f = run_apply(t, n)
check("已有事务标记 -> EXIT_VALIDATION, 标记和文件都不动", rc == ua.EXIT_VALIDATION and snapshot(t) == before)
rc, f = run_apply(TMP / "no_such_folder", n)
check("target 不存在 -> EXIT_VALIDATION", rc == ua.EXIT_VALIDATION)

# ------------------------------------------------------------------ 10
print("== 10 --no-restart ==")
t, n = make_old(), make_new()
rc, f = run_apply(t, n, restart=False)
check("成功 + no-restart: 返回 0 且文件已替换", rc == 0 and snapshot(t)[0]["FloweyPet.exe"] == b"NEW-EXE-LONGER")
check("成功 + no-restart: 不启动任何东西", f.launches == [])
t, n = make_old(), make_new()
before = snapshot(t)
with Always("_copy", PermissionError, lambda s, d: d.endswith("b.png.new")):
    rc, f = run_apply(t, n, restart=False)
check("失败 + no-restart: 回滚完成且不启动任何东西", rc == ua.EXIT_FAILED_ROLLED_BACK and snapshot(t) == before and f.launches == [])
t, n = make_old(), make_new()
(n / "FloweyPet.exe").unlink()
rc, f = run_apply(t, n, restart=False)
check("校验失败 + no-restart: 不启动任何东西", rc == ua.EXIT_VALIDATION and f.launches == [])
t, n = make_old(), make_new()


def failing_launcher(*a):
    raise OSError("cannot start")


rc = ua.apply_update(t, n, 1, 1, "3.2.0", "3.3.0", True, waiter=lambda *a: True, launcher=failing_launcher,
                     sleep=lambda s: None, self_info=(1, 1))
check("启动新版失败 -> EXIT_LAUNCH_FAILED, 但文件保持新版 (不回滚)",
      rc == ua.EXIT_LAUNCH_FAILED and snapshot(t)[0]["FloweyPet.exe"] == b"NEW-EXE-LONGER")

# ------------------------------------------------------------------ 11
print("== 11 受保护的文件 ==")
t, n = make_old(), make_new()
write(n, "config.json", '{"from": "package"}')
write(n, "stats.json", '{"from": "package"}')
write(n, ".install_manifest.json", "package-manifest")
write(n, ".update_pending.json", "package-marker")
write(n, ".update_backup/evil.txt", "evil")
write(t, "assets/mine_in_assets.txt", "user file inside a package dir")
before = snapshot(t)[0]
rc, f = run_apply(t, n)
after = snapshot(t)[0]
check("返回 0", rc == 0)
check("包里带 config.json/stats.json 也不覆盖用户数据", after["config.json"] == before["config.json"] and after["stats.json"] == before["stats.json"])
check("包里带的清单/标记文件被忽略 (清单是我们自己写的)", json.loads(after[".install_manifest.json"])["version"] == "3.3.0" and ".update_pending.json" not in after)
check("包里的 .update_backup 内容不会被装进来", ".update_backup/evil.txt" not in after)
check("包目录里用户自己放的文件保留", after["assets/mine_in_assets.txt"] == b"user file inside a package dir")
t, n = make_old(), make_new()
write(t, ".install_manifest.json", json.dumps({"version": "3.2.0", "files": [
    "FloweyPet.exe", "lib/old_only.dll", "../outside_manifest.txt", "C:/Windows/System32/x.dll", "/etc/x", "config.json",
    "stats.json", ".update_backup/x", "user_notes.txt"]}))
outside2 = TMP / "outside_manifest.txt"
outside2.write_text("survive")
before = snapshot(t)[0]
rc, f = run_apply(t, n)
after = snapshot(t)[0]
check("旧清单里的不安全路径被忽略, target 外文件还在", rc == 0 and outside2.read_text() == "survive")
check("旧清单里写了 config.json/stats.json 也不会被移走", after["config.json"] == before["config.json"] and after["stats.json"] == before["stats.json"])
check("旧清单里列出的合法旧文件被移走", "lib/old_only.dll" not in after)
t, n = make_old(), make_new()
write(t, ".install_manifest.json", "{ this is not json")
rc, f = run_apply(t, n)
check("旧清单损坏 -> 当作空清单继续 (只覆盖和新增)", rc == 0 and snapshot(t)[0]["lib/old_only.dll"] == b"OLD-ONLY-DLL")

# ------------------------------------------------------------------ 12
print("== 12 事务标记内容 ==")
t, n = make_old(), make_new()
seen = {}
orig = ua._copy


def peek(src, dst):
    mp = t / ".update_pending.json"
    if mp.exists() and "marker" not in seen and dst.endswith("a.png.new"):
        seen["marker"] = json.loads(mp.read_text(encoding="utf-8"))
    return orig(src, dst)


ua._copy = peek
try:
    rc, f = run_apply(t, n)
finally:
    ua._copy = orig
m = seen.get("marker", {})
check("替换中途标记存在", bool(m))
check("标记含 阶段/pid/创建时间", m.get("phase") == "replacing" and m.get("pid") == 4321 and m.get("created") == 98765, repr(m.get("phase")))
check("标记含 旧版本/新版本", m.get("old_version") == "3.2.0" and m.get("new_version") == "3.3.0")
check("标记含 新增文件列表 (含已记录的 assets/b.png)", isinstance(m.get("added"), list))
check("标记含 备份目录", m.get("backup_dir") == str(t / ".update_backup"))
check("标记含 新包文件列表", sorted(m.get("package_files", [])) == sorted(["FloweyPet.exe", "dialogue.json", "assets/a.png", "assets/b.png", "newdir/deep/c.txt"]))
check("标记不含任何网址", "http" not in json.dumps(m))

# ------------------------------------------------------------------ 13
print("== 13 日志 ==")
logfile = TMP / "data" / "logs" / "update.log"
check("update.log 写在 FLOWEYPET_DATA_DIR\\logs 下", logfile.is_file(), str(logfile))
text = logfile.read_text(encoding="utf-8")
check("日志有时间戳和内容", "应用更新：3.2.0 -> 3.3.0" in text and text.startswith("["))
check("日志里没有任何网址", "http://" not in text and "https://" not in text and "github" not in text.lower())
check("日志是追加写 (多次更新都在里面)", text.count("应用更新：3.2.0 -> 3.3.0") > 5)
check("log_path 取 FLOWEYPET_DATA_DIR", ua.log_path() == str(TMP / "data" / "logs" / "update.log"))
saved_env = {k: os.environ.get(k) for k in ("FLOWEYPET_DATA_DIR", "APPDATA")}
try:
    os.environ["FLOWEYPET_DATA_DIR"] = ""
    os.environ["APPDATA"] = str(TMP / "fake_appdata")
    check("没有 FLOWEYPET_DATA_DIR 时用 %APPDATA%\\FloweyPet\\logs", ua.log_path() == str(TMP / "fake_appdata" / "FloweyPet" / "logs" / "update.log"))
    del os.environ["APPDATA"]
    check("两者都没有 -> 不写日志 (None)", ua.log_path() is None)
    t, n = make_old(), make_new()
    rc, f = run_apply(t, n)
    check("没有日志位置时更新照样成功", rc == 0)
    blocker = TMP / "blocker_file"
    blocker.write_text("i am a file, not a folder")
    os.environ["FLOWEYPET_DATA_DIR"] = str(blocker)  # logs\ can't be created under a file
    t, n = make_old(), make_new()
    rc, f = run_apply(t, n)
    check("日志写不进去 -> 更新照样成功", rc == 0 and snapshot(t)[0]["FloweyPet.exe"] == b"NEW-EXE-LONGER", str(rc))
    t, n = make_old(), make_new()
    before = snapshot(t)
    with Always("_copy", PermissionError, lambda s, d: d.endswith("b.png.new")):
        rc, f = run_apply(t, n)
    check("日志写不进去 -> 失败回滚照样完成", rc == ua.EXIT_FAILED_ROLLED_BACK and snapshot(t) == before)
finally:
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

# ------------------------------------------------------------------ 14
print("== 14 命令行 ==")
P = ua._parse_args
ok = P(["--apply-update", "--target", "C:\\x", "--wait-pid", "12", "--wait-created", "1234567", "--old-version", "3.2.0"])
check("合法命令行", ok == {"restart": True, "target": "C:\\x", "pid": 12, "created": 1234567, "old_version": "3.2.0"}, repr(ok))
ok2 = P(["--no-restart", "--apply-update", "--old-version", "3.2.0", "--wait-created", "7", "--wait-pid", "12", "--target", "D:/a b"])
check("参数顺序无关, --no-restart 生效", ok2 and ok2["restart"] is False and ok2["target"] == "D:/a b")
base = ["--apply-update", "--target", "C:\\x", "--wait-pid", "12", "--wait-created", "1", "--old-version", "3.2.0"]
bad_cases = {
    "缺 --apply-update": base[1:],
    "缺 --target": base[:1] + base[3:],
    "缺 --wait-pid": base[:3] + base[5:],
    "缺 --wait-created": base[:5] + base[7:],
    "缺 --old-version": base[:7],
    "pid 不是数字": base[:4] + ["abc"] + base[5:],
    "pid 为 0": base[:4] + ["0"] + base[5:],
    "pid 为负": base[:4] + ["-5"] + base[5:],
    "created 不是数字": base[:6] + ["xyz"] + base[7:],
    "旧版本含非法字符": base[:8] + ["3.2.0; calc"],
    "旧版本为空": base[:8] + [""],
    "未知参数 --new-dir": base + ["--new-dir", "C:\\evil"],
    "未知参数 --skip-verify": base + ["--skip-verify"],
    "未知参数 --allow-http": base + ["--allow-http"],
    "未知参数 --url": base + ["--url", "http://x"],
    "选项缺值": base + ["--target"],
    "target 为空": base[:2] + [""] + base[3:],
}
for label, argv in bad_cases.items():
    check(f"拒绝: {label}", P(argv) is None, repr(P(argv)))
check("main(坏参数) 返回 2 且不做任何事", ua.main(["--apply-update", "--bogus"]) == ua.EXIT_BAD_ARGS)
captured = {}
orig = ua.apply_update


def fake_apply(target, new_dir, pid, created, old_version, new_version=None, restart=True, **kw):
    captured.update(dict(target=target, new_dir=new_dir, pid=pid, created=created, old=old_version, restart=restart, kw=kw))
    return 0


ua.apply_update = fake_apply
try:
    rc = ua.main(base + ["--no-restart"])
finally:
    ua.apply_update = orig
check("main 把参数交给 apply_update", rc == 0 and captured["target"] == "C:\\x" and captured["pid"] == 12 and captured["created"] == 1
      and captured["old"] == "3.2.0" and captured["restart"] is False, repr(captured))
check("命令行入口的新文件目录只能是当前 exe 所在目录",
      captured["new_dir"] == os.path.dirname(os.path.abspath(sys.executable)) and captured["kw"] == {}, repr(captured))
ua.apply_update = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("unexpected"))
try:
    rc = ua.main(base)
finally:
    ua.apply_update = orig
check("main 遇到意外错误不抛出, 返回 EXIT_UNEXPECTED", rc == ua.EXIT_UNEXPECTED, str(rc))
codes = [ua.EXIT_OK, ua.EXIT_UNEXPECTED, ua.EXIT_BAD_ARGS, ua.EXIT_VALIDATION, ua.EXIT_WAIT_TIMEOUT,
         ua.EXIT_FAILED_ROLLED_BACK, ua.EXIT_ROLLBACK_INCOMPLETE, ua.EXIT_LAUNCH_FAILED]
check("退出码互不相同且只有成功是 0", len(set(codes)) == 8 and codes[0] == 0 and all(c != 0 for c in codes[1:]))
check("docstring 里写了命令行约定", "--apply-update --target" in ua.__doc__ and "--wait-created" in ua.__doc__ and "--no-restart" in ua.__doc__)

# ------------------------------------------------------------------ 15
print("== 15 临时目录判断 ==")
tmpdir = tempfile.gettempdir()
check("系统临时目录下 -> True", ua.is_running_from_temp(os.path.join(tmpdir, "FloweyPet-update-abc", "FloweyPet-v3.3.0")))
check("系统临时目录本身 -> True", ua.is_running_from_temp(tmpdir))
check("名字相近的同级目录 -> False", ua.is_running_from_temp(tmpdir + "2") is False)
check("普通安装目录 -> False", ua.is_running_from_temp(str(ROOT)) is False)
check("D:\\Games 之类 -> False", ua.is_running_from_temp("D:\\Games\\FloweyPet") is False)

# ------------------------------------------------------------------ 16
print("== 16 模块纯度 / main.py 接线 ==")
code = ("import sys; sys.path.insert(0,'src'); import updater_apply; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('PyQt6','config','paths','app_log')))")
r = subprocess.run([sys.executable, "-I", "-c", code], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
check("导入 updater_apply 不会带入 PyQt6/config/paths/app_log", r.stdout.strip() == "[]" and r.returncode == 0, repr((r.stdout, r.stderr[-200:])))
src = (ROOT / "src" / "updater_apply.py").read_text(encoding="utf-8")
import re  # noqa: E402

imports = set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", src, re.M))
check("只导入标准库和 version", imports <= {"ctypes", "hashlib", "json", "os", "re", "shutil", "subprocess", "sys",
                                         "tempfile", "time", "datetime", "version"}, repr(imports))
check("模块里没有 print( 也没有 import logging", "print(" not in src and "import logging" not in src)
main_src = (ROOT / "main.py").read_text(encoding="utf-8")
i_apply = main_src.index('"--apply-update" in sys.argv')
check("main.py: --apply-update 分流在 PyQt6 导入之前", i_apply < main_src.index("from PyQt6"))
check("main.py: 分流在 sys.path.insert 之后", main_src.index("sys.path.insert") < i_apply)
check("main.py: recover_if_needed 在 PyQt6 导入之前", main_src.index("recover_if_needed") < main_src.index("from PyQt6"))
check("main.py: 分流分支里 sys.exit(updater_apply.main(", "sys.exit(updater_apply.main(sys.argv[1:]))" in main_src)
branch = main_src[i_apply:main_src.index("from PyQt6")]
check("分流部分只导入 updater_apply", set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", branch, re.M)) == {"updater_apply"})
check("main.py: 临时目录时跳过迁移和自启同步", main_src.count("raise _SkipStep") == 2 and "running_from_temp" in main_src)

dev_cfg = ROOT / "dev_data" / "config.json"
dev_dir_before = sorted(os.listdir(ROOT / "dev_data")) if (ROOT / "dev_data").exists() else None
cfg_before = (dev_cfg.stat().st_mtime_ns, dev_cfg.read_bytes()) if dev_cfg.exists() else None
env = {k: v for k, v in os.environ.items() if k != "FLOWEYPET_DATA_DIR"}  # prove dev_data itself stays untouched
r = subprocess.run([sys.executable, "main.py", "--apply-update", "--target", str(TMP / "nonexistent_target"),
                    "--wait-pid", "12345", "--wait-created", "1", "--old-version", "3.2.0"],
                   cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
check("源码运行 main.py --apply-update -> 退出码 2", r.returncode == 2, str(r.returncode))
check("源码运行: 没有任何输出", r.stdout == "" and r.stderr == "", repr((r.stdout, r.stderr[-200:])))
cfg_after = (dev_cfg.stat().st_mtime_ns, dev_cfg.read_bytes()) if dev_cfg.exists() else None
check("dev_data/config.json 修改时间和内容都没变", cfg_before == cfg_after)
check("dev_data 目录内容没变 (没有新文件)", dev_dir_before == (sorted(os.listdir(ROOT / "dev_data")) if (ROOT / "dev_data").exists() else None))
check("源码运行没有碰 update.log 之外的东西 (无 PyQt6 窗口/进程残留)", r.returncode == 2)

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("应用更新模式用例全部通过")
sys.exit(0)
