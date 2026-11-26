# -*- coding: utf-8 -*-
"""Acceptance tests for src/update_download.py (streaming downloader).

Everything runs against a throwaway http.server on 127.0.0.1. The default
validator is strict (https + GitHub hosts only), so every test that really
connects injects its own looser `url_validator` through the function
argument -- the production default is never changed. The strict default is
tested separately, both directly and through the redirect handler.

Run:  python test_update_download_acceptance.py
"""
import hashlib
import http.server
import os
import socket
import sys
import tempfile
import threading
import time
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import update_download as ud  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


TMP = Path(tempfile.mkdtemp(prefix="floweypet_dl_"))

PAYLOAD = bytes((i * 7 + 3) % 256 for i in range(300_000))
SHA = hashlib.sha256(PAYLOAD).hexdigest()
SIZE = len(PAYLOAD)

REQUESTS = []  # paths the server saw
SENT = {"bytes": 0}


class Handler(http.server.BaseHTTPRequestHandler):
    # HTTP/1.0: connection closes after each response, so a body without a
    # Content-Length is delimited by closing -- lets us stream "extra" bytes.
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", headers=()):
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        path = self.path
        REQUESTS.append(path)
        try:
            if path == "/good":
                self._send(200, headers=[("Content-Length", str(SIZE))])
                self.wfile.write(PAYLOAD)
            elif path == "/badhash":
                data = bytes(reversed(PAYLOAD))
                self._send(200, headers=[("Content-Length", str(len(data)))])
                self.wfile.write(data)
            elif path == "/short":  # honest Content-Length, fewer bytes than expected
                data = PAYLOAD[:SIZE - 1000]
                self._send(200, headers=[("Content-Length", str(len(data)))])
                self.wfile.write(data)
            elif path == "/truncated":  # promises SIZE, closes early
                self._send(200, headers=[("Content-Length", str(SIZE))])
                self.wfile.write(PAYLOAD[:SIZE // 2])
            elif path == "/bigheader":  # declares more than expected
                data = PAYLOAD + b"x" * 5000
                self._send(200, headers=[("Content-Length", str(len(data)))])
                self.wfile.write(data)
            elif path == "/stream_extra":  # no Content-Length, keeps sending
                self._send(200)
                chunk = b"y" * 65536
                for _ in range(80):  # ~5MB, expected is 300KB
                    self.wfile.write(chunk)
                    SENT["bytes"] += len(chunk)
            elif path == "/slow":
                self._send(200, headers=[("Content-Length", str(SIZE))])
                for i in range(0, SIZE, 10_000):
                    self.wfile.write(PAYLOAD[i:i + 10_000])
                    self.wfile.flush()
                    time.sleep(0.05)
            elif path == "/stall":  # headers then silence
                self._send(200, headers=[("Content-Length", str(SIZE))])
                self.wfile.flush()
                time.sleep(3)
            elif path == "/redir_good":
                self._send(302, headers=[("Location", "/good?sig=SECRET123"), ("Content-Length", "0")])
            elif path.startswith("/good?sig="):
                self._send(200, headers=[("Content-Length", str(SIZE))])
                self.wfile.write(PAYLOAD)
            elif path == "/redir_evil":
                self._send(302, headers=[("Location", "https://evil.example/payload?sig=SECRET123"), ("Content-Length", "0")])
            elif path == "/redir_http_other":
                self._send(302, headers=[("Location", "http://127.0.0.2:1/x?sig=SECRET123"), ("Content-Length", "0")])
            elif path == "/redir_to_404":
                self._send(302, headers=[("Location", "/nothing?sig=SECRET123"), ("Content-Length", "0")])
            else:
                self._send(404, headers=[("Content-Length", "0")])
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


server = Server(("127.0.0.1", 0), Handler)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"

# the loosened validator used ONLY by these tests, passed as an argument
local_validator = ud.build_url_validator(allowed_hosts=("127.0.0.1",), allowed_schemes=("http", "https"))


def attempt(path, dest, **kw):
    """Returns (error_or_None, result)."""
    kw.setdefault("url_validator", local_validator)
    kw.setdefault("timeout", 5)
    try:
        r = ud.download(BASE + path, dest, kw.pop("sha", SHA), kw.pop("size", SIZE), **kw)
        return None, r
    except ud.UpdateDownloadError as e:
        return e, None


def part_of(dest):
    return Path(str(dest) + ".part")


# ------------------------------------------------------------------ 1
print("== 1 正常下载 ==")
dest = TMP / "ok" / "pkg.zip"
calls = []
err, res = attempt("/good", dest, progress_cb=lambda d, t: calls.append((d, t)))
check("正常下载无异常", err is None, repr(err))
check("返回 dest_path", res == str(dest))
check("文件内容正确", dest.exists() and dest.read_bytes() == PAYLOAD)
check("成功后 .part 不存在", not part_of(dest).exists())
check("progress_cb 被调用且单调", len(calls) > 1 and all(a[0] < b[0] for a, b in zip(calls, calls[1:])))
check("progress_cb 最后一次 = (总大小, 总大小)", calls[-1] == (SIZE, SIZE))
check("progress_cb 的第二个参数始终是 expected_size", all(t == SIZE for _, t in calls))
err, res = attempt("/good", TMP / "ok2" / "pkg.zip", sha=SHA.upper())
check("expected_sha256 大写也接受", err is None, repr(err))
err, res = attempt("/good", TMP / "ok3.zip", progress_cb=lambda d, t: 1 / 0)
check("progress_cb 抛异常不影响下载", err is None and (TMP / "ok3.zip").read_bytes() == PAYLOAD, repr(err))
err, res = attempt("/redir_good", TMP / "ok4.zip")
check("同主机重定向 + 查询串可下载", err is None and (TMP / "ok4.zip").read_bytes() == PAYLOAD, repr(err))
stale = TMP / "stale.zip"
part_of(stale).write_bytes(b"leftover garbage from an earlier run")
err, res = attempt("/good", stale)
check("已有残留 .part 被覆盖并成功", err is None and stale.read_bytes() == PAYLOAD and not part_of(stale).exists(), repr(err))
old = TMP / "overwrite.zip"
old.write_bytes(b"old content")
err, res = attempt("/good", old)
check("dest 已存在时被原子替换", err is None and old.read_bytes() == PAYLOAD)

# ------------------------------------------------------------------ 2
print("== 2 校验失败 ==")
dest = TMP / "badhash.zip"
dest.write_bytes(b"previous good file")
err, _ = attempt("/badhash", dest)
check("hash 不对 -> hash_mismatch", err is not None and err.code == "hash_mismatch", repr(err))
check("hash 不对: .part 已删除", not part_of(dest).exists())
check("hash 不对: 原 dest 未被改动", dest.read_bytes() == b"previous good file")

dest = TMP / "short.zip"
err, _ = attempt("/short", dest)
check("服务器声明更小 -> size_mismatch", err is not None and err.code == "size_mismatch", repr(err))
check("size_mismatch: 没有 dest 也没有 .part", not dest.exists() and not part_of(dest).exists())

dest = TMP / "truncated.zip"
err, _ = attempt("/truncated", dest)
check("连接提前断开 -> size_mismatch 或 network", err is not None and err.code in ("size_mismatch", "network"), repr(err))
check("提前断开: 没有 dest 也没有 .part", not dest.exists() and not part_of(dest).exists())

dest = TMP / "sizearg.zip"
err, _ = attempt("/good", dest, size=SIZE - 1)
check("expected_size 比实际小 -> too_large", err is not None and err.code == "too_large", repr(err))
err, _ = attempt("/good", dest, size=SIZE + 1)
check("expected_size 比实际大 -> size_mismatch", err is not None and err.code == "size_mismatch", repr(err))
check("size 参数不对: 无残留", not dest.exists() and not part_of(dest).exists())

# ------------------------------------------------------------------ 3
print("== 3 服务器多发字节 ==")
dest = TMP / "bigheader.zip"
err, _ = attempt("/bigheader", dest)
check("Content-Length 大于期望 -> too_large", err is not None and err.code == "too_large", repr(err))
check("bigheader: 无残留", not dest.exists() and not part_of(dest).exists())

dest = TMP / "stream_extra.zip"
seen = []
err, _ = attempt("/stream_extra", dest, progress_cb=lambda d, t: seen.append(d))
check("无 Content-Length 一直发 -> too_large", err is not None and err.code == "too_large", repr(err))
check("读到的字节没超过期望 (立刻失败)", not seen or max(seen) <= SIZE, repr(max(seen) if seen else None))
check("stream_extra: 无残留", not dest.exists() and not part_of(dest).exists())

# ------------------------------------------------------------------ 4
print("== 4 默认严格校验 ==")
strict = ud.strict_url_validator


def code_of(fn, *a):
    try:
        fn(*a)
        return None
    except ud.UpdateDownloadError as e:
        return e.code


GH = "https://github.com/winwinking/FloweyPet/releases/download/v3.3.0/FloweyPet-v3.3.0.zip"
check("默认: github.com https 通过", code_of(strict, GH) is None)
check("默认: release-assets 通过", code_of(strict, "https://release-assets.githubusercontent.com/x?sig=1") is None)
check("默认: objects 通过", code_of(strict, "https://objects.githubusercontent.com/x") is None)
check("默认: 大写主机名通过", code_of(strict, "https://GitHub.com/x") is None)
check("默认: http 地址 -> bad_url", code_of(strict, GH.replace("https://", "http://")) == "bad_url")
check("默认: ftp/file -> bad_url", code_of(strict, "ftp://github.com/x") == "bad_url" and code_of(strict, "file:///C:/x") == "bad_url")
check("默认: 其他主机 -> bad_host", code_of(strict, "https://evil.example/x") == "bad_host")
check("默认: 127.0.0.1 -> bad_host", code_of(strict, "https://127.0.0.1/x") == "bad_host")
check("默认: 子域名欺骗 -> bad_host", code_of(strict, "https://github.com.evil.example/x") == "bad_host")
check("默认: 后缀欺骗 -> bad_host", code_of(strict, "https://evilgithub.com/x") == "bad_host")
check("默认: 用户信息欺骗 -> bad_url", code_of(strict, "https://github.com@evil.example/x") == "bad_url")
check("默认: 带用户名 -> bad_url", code_of(strict, "https://user:pw@github.com/x") == "bad_url")
check("默认: 非 443 端口 -> bad_url", code_of(strict, "https://github.com:8443/x") == "bad_url")
check("默认: 非字符串 -> bad_url", code_of(strict, None) == "bad_url" and code_of(strict, 123) == "bad_url")
check("默认: 空串 -> bad_url", code_of(strict, "") == "bad_url")

dest = TMP / "strict_http.zip"
n_before = len(REQUESTS)
try:
    ud.download(BASE + "/good", dest, SHA, SIZE, timeout=5)  # default validator on purpose
    e = None
except ud.UpdateDownloadError as ex:
    e = ex
check("download() 默认校验拒绝 http://127.0.0.1", e is not None and e.code == "bad_url", repr(e))
check("被拒绝时没有发出任何请求", len(REQUESTS) == n_before)
check("被拒绝时没有 dest/.part", not dest.exists() and not part_of(dest).exists())

# redirect hops are validated, using the strict default
handler = ud._ValidatingRedirect(ud.strict_url_validator)
import urllib.request  # noqa: E402

req = urllib.request.Request(GH)
for label, target, want in (
    ("重定向到 http", "http://github.com/x", "bad_url"),
    ("重定向到白名单外主机", "https://evil.example/x?sig=SECRET123", "bad_host"),
    ("重定向到 file://", "file:///C:/Windows/win.ini", "bad_url"),
):
    try:
        handler.redirect_request(req, None, 302, "Found", {}, target)
        got = None
    except ud.UpdateDownloadError as ex:
        got = ex.code
    check(f"默认严格: {label} 被拒 ({want})", got == want, repr(got))
try:
    handler.redirect_request(req, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/a?sig=1")
    ok = True
except Exception:  # noqa: BLE001
    ok = False
check("默认严格: 重定向到白名单内 https 主机允许", ok)

# end to end: redirect hop rejected while the initial URL was acceptable
dest = TMP / "redir_evil.zip"
err, _ = attempt("/redir_evil", dest)
check("下载中遇到跳转到白名单外主机 -> bad_host", err is not None and err.code == "bad_host", repr(err))
check("跳转被拒: 无残留", not dest.exists() and not part_of(dest).exists())
check("跳转被拒: 异常信息不含签名参数", err is not None and "SECRET123" not in str(err) and "sig=" not in str(err), str(err))
dest = TMP / "redir_http.zip"
v_https_only_local = ud.build_url_validator(allowed_hosts=("127.0.0.1",), allowed_schemes=("https",))
err, _ = attempt("/good", dest, url_validator=v_https_only_local)
check("validator 只允许 https 时 http 初始地址 -> bad_url", err is not None and err.code == "bad_url", repr(err))

# ------------------------------------------------------------------ 5
print("== 5 取消 ==")
dest = TMP / "cancel.zip"
ev = threading.Event()
seen = []


def cb(done, total):
    seen.append(done)
    if done >= 20_000:
        ev.set()


err, _ = attempt("/slow", dest, progress_cb=cb, cancel_event=ev)
check("下载中取消 -> cancelled", err is not None and err.code == "cancelled", repr(err))
check("取消后 .part 与 dest 都不存在", not dest.exists() and not part_of(dest).exists())
check("取消后很快停止 (没有读完整个文件)", seen and max(seen) < SIZE)
ev2 = threading.Event()
ev2.set()
n_before = len(REQUESTS)
err, _ = attempt("/good", TMP / "cancel2.zip", cancel_event=ev2)
check("开始前已取消 -> cancelled 且不发请求", err is not None and err.code == "cancelled" and len(REQUESTS) == n_before, repr(err))
err, _ = attempt("/good", TMP / "cancel3.zip", cancel_event=threading.Event())
check("未触发的 cancel_event 不影响下载", err is None, repr(err))

# ------------------------------------------------------------------ 6
print("== 6 网络错误 / 超时 ==")
dest = TMP / "notfound.zip"
err, _ = attempt("/missing", dest)
check("HTTP 404 -> network", err is not None and err.code == "network", repr(err))
check("404: 无残留", not dest.exists() and not part_of(dest).exists())
s = socket.socket()
s.bind(("127.0.0.1", 0))
dead_port = s.getsockname()[1]
s.close()
try:
    ud.download(f"http://127.0.0.1:{dead_port}/x", TMP / "refused.zip", SHA, SIZE, timeout=3, url_validator=local_validator)
    e = None
except ud.UpdateDownloadError as ex:
    e = ex
check("连接被拒 -> network", e is not None and e.code == "network", repr(e))
t0 = time.time()
dest = TMP / "stall.zip"
err, _ = attempt("/stall", dest, timeout=1)
check("读超时 -> network", err is not None and err.code == "network", repr(err))
check("读超时按单次读取计 (约 1 秒, 不是等 3 秒)", time.time() - t0 < 2.8, f"{time.time() - t0:.2f}s")
check("读超时: 无残留", not dest.exists() and not part_of(dest).exists())
dest = TMP / "redir404.zip"
err, _ = attempt("/redir_to_404", dest)
check("跳转后 404 -> network, 异常信息不含签名", err is not None and err.code == "network" and "SECRET123" not in str(err) and "SECRET123" not in repr(err.args), repr(err))

# ------------------------------------------------------------------ 7
print("== 7 磁盘空间 ==")
Usage = namedtuple("Usage", "total used free")
real_usage = ud.shutil.disk_usage
n_before = len(REQUESTS)
try:
    ud.shutil.disk_usage = lambda p: Usage(10**12, 0, SIZE * 3 - 1)
    dest = TMP / "nospace.zip"
    err, _ = attempt("/good", dest)
    check("剩余空间 < 3 倍 -> no_space", err is not None and err.code == "no_space", repr(err))
    check("no_space: 没发请求、无残留", len(REQUESTS) == n_before and not dest.exists() and not part_of(dest).exists())
    ud.shutil.disk_usage = lambda p: Usage(10**12, 0, SIZE * 3)
    err, _ = attempt("/good", TMP / "exact3x.zip")
    check("剩余空间恰好 3 倍 -> 允许", err is None, repr(err))
    seen_paths = []
    ud.shutil.disk_usage = lambda p: (seen_paths.append(str(p)), Usage(10**12, 0, 10**12))[1]
    err, _ = attempt("/good", TMP / "sub_dir" / "x.zip")
    check("空间检查的是目标所在目录", err is None and seen_paths and Path(seen_paths[0]) == TMP / "sub_dir", repr(seen_paths))

    def boom(p):
        raise OSError("nope")

    ud.shutil.disk_usage = boom
    err, _ = attempt("/good", TMP / "boom.zip")
    check("查询剩余空间失败 -> no_space", err is not None and err.code == "no_space", repr(err))
finally:
    ud.shutil.disk_usage = real_usage

# ------------------------------------------------------------------ 8
print("== 8 其他 ==")
seen_ua = []
orig_do_get = Handler.do_GET


def ua_get(self):
    seen_ua.append(self.headers.get("User-Agent"))
    return orig_do_get(self)


Handler.do_GET = ua_get
import version  # noqa: E402

attempt("/good", TMP / "ua.zip")
Handler.do_GET = orig_do_get
check("User-Agent = FloweyPet/<version>", seen_ua and seen_ua[0] == f"FloweyPet/{version.VERSION}", repr(seen_ua))
for bad in (("zz", SIZE), (SHA, 0), (SHA, -1), (SHA, True), (SHA, "10"), (None, SIZE)):
    try:
        ud.download(BASE + "/good", TMP / "badargs.zip", bad[0], bad[1], url_validator=local_validator)
        got = None
    except ValueError:
        got = "ValueError"
    except Exception as ex:  # noqa: BLE001
        got = type(ex).__name__
    check(f"非法参数 {bad[0] if bad[0] is None else str(bad[0])[:6]!r},{bad[1]!r} -> ValueError", got == "ValueError", got)
e = ud.UpdateDownloadError("hash_mismatch", "sha256 does not match")
check("异常带 code/detail 且 str 含 code", e.code == "hash_mismatch" and e.detail and str(e).startswith("hash_mismatch"))
codes = {ud.NETWORK, ud.BAD_URL, ud.BAD_HOST, ud.TOO_LARGE, ud.SIZE_MISMATCH, ud.HASH_MISMATCH, ud.NO_SPACE, ud.CANCELLED}
check("8 个原因代码齐全", codes == {"network", "bad_url", "bad_host", "too_large", "size_mismatch", "hash_mismatch", "no_space", "cancelled"})
src = (Path(__file__).resolve().parent / "src" / "update_download.py").read_text(encoding="utf-8")
import re  # noqa: E402

imports = set(re.findall(r"^(?:import|from)\s+([A-Za-z_][\w]*)", src, re.M))
check("模块只导入标准库和 version", imports <= {"hashlib", "http", "os", "re", "shutil", "urllib", "version"}, repr(imports))
check("模块没有任何 print/logging", "print(" not in src and "logging" not in src)
check("默认参数是严格校验", ud.download.__defaults__[-1] is ud.strict_url_validator)

server.shutdown()
print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("下载器用例全部通过")
sys.exit(0)
