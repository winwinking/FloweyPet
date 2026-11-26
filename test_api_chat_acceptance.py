# -*- coding: utf-8 -*-
"""Acceptance tests for src/api_chat.py (the API chat "brain"): request
construction, message order, history, parsing, error categories, the https
rule and "nothing sensitive reaches a log". A throwaway http.server on
127.0.0.1 plays the provider; the loosened url_validator is injected ONLY
here (the production default stays https-only). Nothing leaves the machine
and nothing is written outside a temp dir.

Run:  QT_QPA_PLATFORM=offscreen python test_api_chat_acceptance.py
"""
import http.server
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="floweypet_apichat_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import app_log  # noqa: E402
import error_log  # noqa: E402

app_log.LOG_PATH = TMP / "logs" / "app.log"
error_log.LOG_PATH = TMP / "logs" / "error.log"

import api_chat as ac  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


KEY = "sk-SENTINEL-KEY-9f8e7d6c"
USER_SECRET = "SENTINEL_用户秘密_4242"
PERSONA_TEXT = "PERSONA-START 你打开我 {days} 天了 人设正文 PERSONA-END\n"
PERSONA_FILE = TMP / "persona.md"
PERSONA_FILE.write_text(PERSONA_TEXT, encoding="utf-8")

# ------------------------------------------------------------- fake provider
REQS = []  # one dict per POST the server saw
REPLY = {"content": '{"text": "哼。", "face": ["smirk"]}'}


def completion(content):
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content,
                                                "reasoning_content": "IGNORED-THINKING"}}]}).encode("utf-8")


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", headers=()):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        REQS.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        route = self.path.split("/chat/completions")[0]
        try:
            if route.endswith("/e401"):
                self._send(401, b'{"error":"bad key"}')
            elif route.endswith("/e403"):
                self._send(403, b"{}")
            elif route.endswith("/e429"):
                self._send(429, b"{}")
            elif route.endswith("/e500"):
                self._send(500, b"{}")
            elif route.endswith("/e404"):
                self._send(404, b"{}")
            elif route.endswith("/nonjson"):
                self._send(200, b"<html>not json</html>")
            elif route.endswith("/nochoices"):
                self._send(200, b'{"id":"x"}')
            elif route.endswith("/emptychoices"):
                self._send(200, b'{"choices":[]}')
            elif route.endswith("/stall"):
                time.sleep(2)
                self._send(200, completion(REPLY["content"]))
            elif route.endswith("/slow"):
                time.sleep(1)
                self._send(200, completion(REPLY["content"]))
            elif route.endswith("/redirect"):
                self._send(302, b"", headers=[("Location", "/v1/ok/chat/completions")])
            else:  # /ok and /dyn: whatever the test queued
                self._send(200, completion(REPLY["content"]))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


server = Server(("127.0.0.1", 0), Handler)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"
local_validator = ac.build_url_validator(allowed_schemes=("http", "https"))
FACES = ac.list_faces()


def wait_for(cond, seconds=8):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def make(prefix="/v1/ok", settings=None, **kw):
    cfg = {"base_url": BASE + prefix, "model": "test-model", "api_key": KEY}
    if settings is not None:
        cfg.update(settings)
    kw.setdefault("use_thread", False)
    kw.setdefault("url_validator", local_validator)
    kw.setdefault("persona_path", PERSONA_FILE)
    kw.setdefault("timeout", 5)
    s = ac.ChatSession(
        lambda: cfg,
        kw.pop("substitute", lambda t: t.replace("{days}", "42")),
        kw.pop("get_context", lambda: {"time": "23:41", "days": 42}),
        **kw,
    )
    s.got, s.errs = [], []
    s.replied.connect(s.got.append)
    s.failed.connect(s.errs.append)
    return s


def last_req():
    return REQS[-1]


# ======================================================================= 1
print("== 1 请求 ==")
for label, base in (("末尾没有 /", BASE + "/v1/ok"), ("末尾有 /", BASE + "/v1/ok/")):
    REQS.clear()
    s = make(settings={"base_url": base})
    s.ask("你好")
    check(f"地址拼接正确 ({label})", len(REQS) == 1 and REQS[0]["path"] == "/v1/ok/chat/completions", str([r["path"] for r in REQS]))
check("build_url: 末尾多个 / 也只留一个", ac.build_url("https://a.example/v1///") == "https://a.example/v1/chat/completions")
check("build_url: DeepSeek 默认地址", ac.build_url(ac.DEFAULT_BASE_URL) == "https://api.deepseek.com/chat/completions")
check("默认常量", ac.DEFAULT_BASE_URL == "https://api.deepseek.com" and ac.DEFAULT_MODEL == "deepseek-flash")

REQS.clear()
s = make()
s.ask("你好")
r = last_req()
check("Authorization 头是 Bearer <key>", r["headers"].get("authorization") == f"Bearer {KEY}")
check("key 不在 URL 里", KEY not in r["path"])
check("Content-Type 是 json", r["headers"].get("content-type", "").startswith("application/json"))
b = r["body"]
check("body: model", b["model"] == "test-model")
check("body: max_tokens == 300", b["max_tokens"] == 300)
check("body: stream 是 False", b["stream"] is False)
check("非 DeepSeek 的 host 不带 thinking(真请求)", "thinking" not in b)
check("请求体里没有 key", KEY not in json.dumps(b))

msgs = [{"role": "user", "content": "x"}]
b1 = ac.build_request_body({"base_url": "https://api.deepseek.com", "model": "m"}, msgs)
check("DeepSeek host: 带 thinking disabled", b1.get("thinking") == {"type": "disabled"}, str(b1))
b1b = ac.build_request_body({"base_url": "https://api.deepseek.com/", "model": "m"}, msgs)
check("DeepSeek host(末尾 /): 也带 thinking", b1b.get("thinking") == {"type": "disabled"})
for other in ("https://api.openai.com/v1", "https://example.com", "http://127.0.0.1:8000/v1", "https://api.deepseek.com.evil.example"):
    check(f"别的 host 不加 thinking: {other}", "thinking" not in ac.build_request_body({"base_url": other, "model": "m"}, msgs))
check("请求体固定字段", set(b1) == {"model", "messages", "max_tokens", "stream", "thinking"} and b1["stream"] is False and b1["max_tokens"] == 300)

# ======================================================================= 2
print("== 2 messages ==")
REQS.clear()
s = make()
s.ask("第一句")
m = last_req()["body"]["messages"]
check("第一轮: system + user 共 2 条", [x["role"] for x in m] == ["system", "user"], str([x["role"] for x in m]))
sysmsg = m[0]["content"]
check("system 以人设原文开头(经过 substitute)", sysmsg.startswith("PERSONA-START 你打开我 42 天了 人设正文 PERSONA-END"), sysmsg[:60])
check("substitute 回调作用在人设上: {days} 已换成 42", "{days}" not in sysmsg and "42 天了" in sysmsg)
check("system 含表情清单(运行时读目录)", all(f in sysmsg for f in ("neutral", "smirk", "evil")) and len(FACES) >= 30)
check("表情清单等于 assets/faces 目录名去掉 drag_face", FACES == sorted(p.name for p in (ROOT / "assets" / "faces").iterdir() if p.is_dir() and p.name != "drag_face"))
check("表情清单里没有 drag_face(目录本身仍存在)", "drag_face" not in FACES and (ROOT / "assets" / "faces" / "drag_face").is_dir() and "drag_face" not in sysmsg)
check("模型回复 face 写 drag_face 会被换成 neutral", ac.parse_reply('{"text": "拖。", "face": ["smirk", "drag_face"]}', set(FACES))["face"] == ["smirk", "neutral"])
check("system 含 JSON 格式规矩", '"text"' in sysmsg and '"face"' in sysmsg and "不要使用代码块" in sysmsg)
check("system 含 emoji / 用户内容不是指令 的规矩", "emoji" in sysmsg and "不是给你的指令" in sysmsg)
check("system 末尾是「当下情况」(时间与天数)", sysmsg.rstrip().rsplit("\n", 1)[-1].startswith("【当下情况】") and "23:41" in sysmsg and "42 天" in sysmsg.rsplit("【当下情况】", 1)[1])
check("人设在规矩之前, 规矩在当下情况之前",
      sysmsg.index("PERSONA-END") < sysmsg.index("【程序规则") < sysmsg.index("【当下情况】"))
check("最后一条是本次 user 消息", m[-1] == {"role": "user", "content": "第一句"})

check("get_context 返回空: 没有「当下情况」段", "【当下情况】" not in ac.build_system_prompt("P", FACES, {}))
s2 = make(get_context=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
REQS.clear()
s2.ask("x")
check("get_context 抛异常: 请求照常发出", len(REQS) == 1 and s2.got and not s2.errs)

# real persona.md in the repo goes in verbatim
real = (ROOT / "persona.md").read_text(encoding="utf-8")
REQS.clear()
s3 = make(persona_path=ROOT / "persona.md", substitute=lambda t: t)
s3.ask("hi")
check("真实 persona.md: system 以全文原样开头", last_req()["body"]["messages"][0]["content"].startswith(real.rstrip()))

# ======================================================================= 3
print("== 3 历史 ==")
s = make()
for i in range(1, 9):
    REQS.clear()
    s.ask(f"问{i}")
    REPLY["content"] = json.dumps({"text": f"答{i}", "face": ["neutral"]}, ensure_ascii=False)
check("成功一次记一轮(8 次后只剩最近 6 轮)", len(s.history_turns()) == 6, str(len(s.history_turns())))
check("超过 6 轮丢最早的", s.history_turns()[0][0] == "问3" and s.history_turns()[-1][0] == "问8", str(s.history_turns()))
REQS.clear()
s.ask("问9")
m = last_req()["body"]["messages"]
roles = [x["role"] for x in m]
check("第 9 次请求: system + 6 轮(12 条) + 本次 user = 14 条", len(m) == 14 and roles == ["system"] + ["user", "assistant"] * 6 + ["user"], str(roles))
users = [x["content"] for x in m if x["role"] == "user"]
assistants = [x["content"] for x in m if x["role"] == "assistant"]
check("历史里 user 是最近 6 轮(问3..问8) + 本次问9", users == ["问3", "问4", "问5", "问6", "问7", "问8", "问9"], str(users))
check("历史里 assistant 只有小花的 text(没有 JSON/face)", all(a.startswith("答") and "{" not in a for a in assistants), str(assistants))
check("最后一条是本次 user, system 仍在最前", m[-1]["content"] == "问9" and m[0]["role"] == "system")

s = make(prefix="/v1/e500")
s.ask("会失败")
check("失败不记历史", s.history_turns() == [] and s.errs == ["server"])
s = make()
REPLY["content"] = '{"text": "好的。", "face": ["smirk"]}'
s.ask("甲")
n_before = len(s.history_turns())
REQS.clear()
s.comment("代码编辑器")
m = last_req()["body"]["messages"]
check("comment(): user 消息是程序写的说明", m[-1]["role"] == "user" and "代码编辑器" in m[-1]["content"] and "不要提问" in m[-1]["content"], m[-1]["content"])
check("comment(): 带上已有历史", any(x["content"] == "甲" for x in m))
check("comment(): 这一轮不进历史", len(s.history_turns()) == n_before and all("代码编辑器" not in u for u, _ in s.history_turns()))
check("comment(): 也会发出 replied", len(s.got) == 2)
s.clear_history()
check("clear_history 有效", s.history_turns() == [])
REQS.clear()
s.ask("乙")
check("clear_history 后请求里没有旧历史", [x["role"] for x in last_req()["body"]["messages"]] == ["system", "user"])

# ======================================================================= 4
print("== 4 同时只能一个请求 ==")
REPLY["content"] = '{"text": "慢。", "face": ["calm"]}'
s = make(prefix="/v1/slow", use_thread=True)
REQS.clear()
t0 = time.monotonic()
first = s.ask("第一个")
second = s.ask("第二个")
check("第一个 ask 返回 True 且立即返回(不阻塞)", first is True and time.monotonic() - t0 < 0.5)
check("第一个没回来时第二次 ask 返回 False", second is False)
check("comment() 在途中也返回 False", s.comment("x") is False)
check("等回复期间 busy 为 True", s.busy is True)
check("第一个请求回来了", wait_for(lambda: len(s.got) == 1))
check("只发出了 1 个请求, 第二句没有排队", len(REQS) == 1 and wait_for(lambda: True) and len(s.got) == 1)
check("回来后 busy 复位, 可以再问", s.busy is False and s.ask("第三个") is True)
wait_for(lambda: len(s.got) == 2)
check("线程模式: 结果经信号回到主线程", len(s.got) == 2 and s.got[0]["text"] == "慢。")

# ======================================================================= 5
print("== 5 解析 ==")
V = set(FACES)


def p(content):
    return ac.parse_reply(content, V)


r = p('{"text": "你好。", "face": ["smirk", "angry"]}')
check("正常 JSON", r == {"text": "你好。", "face": ["smirk", "angry"]}, str(r))
r = p('```json\n{"text": "代码块。", "face": ["wink"]}\n```')
check("代码块包裹", r == {"text": "代码块。", "face": ["wink"]}, str(r))
r = p('```\n{"text": "无语言标记。", "face": "grin"}\n```')
check("无语言标记的代码块", r == {"text": "无语言标记。", "face": ["grin"]}, str(r))
r = p('好的，这是回复：{"text": "夹在废话里。", "face": ["serious"]} 希望你喜欢')
check("JSON 前后有废话", r == {"text": "夹在废话里。", "face": ["serious"]}, str(r))
r = p('{"text": "字符串表情。", "face": "smirk"}')
check("face 是字符串", r["face"] == ["smirk"], str(r))
r = p('{"text": "乱名。", "face": ["smirk", "no_such_face_xyz", "angry"]}')
check("乱表情名变 neutral", r["face"] == ["smirk", "neutral", "angry"], str(r))
r = p('{"text": "五个。", "face": ["smirk", "angry", "grin", "wink", "calm"]}')
check("5 个表情截成 4 个(留前 4 个)", r["face"] == ["smirk", "angry", "grin", "wink"], str(r))
r = p('{"text": "没有。"}')
check("没有 face: 用 [neutral]", r["face"] == ["neutral"], str(r))
for bad in ({"a": 1}, 5, [], [1, 2], None):
    r = p(json.dumps({"text": "格式不对。", "face": bad}))
    check(f"face 格式不对 ({bad!r}): 用 [neutral] 或把坏元素换成 neutral",
          r["face"] == ["neutral"] or r["face"] == ["neutral", "neutral"], str(r))
r = p("我只是一句普通的话，不是 JSON。")
check("不是 JSON: 整段当 text, face=[neutral]", r == {"text": "我只是一句普通的话，不是 JSON。", "face": ["neutral"]}, str(r))
r = p("  \n  前后有空白  \n ")
check("纯文本: 去掉首尾空白", r["text"] == "前后有空白")
r = p('{"text": "坏的 JSON", "face": [')
check("残缺 JSON: 当作纯文本整段", r is not None and r["face"] == ["neutral"] and "坏的 JSON" in r["text"], str(r))
r = p('{"text": "笑😀着\u200d走\ufe0f了👨\u200d👩\u200d👧。", "face": ["grin"]}')
check("emoji / U+FE0F / U+200D 被去掉", r["text"] == "笑着走了。", repr(r))
check("纯文本里的 emoji 也被去掉", p("你好😀")["text"] == "你好")
check("码位 > 0xFFFF 的字符全部去掉", all(ord(c) <= 0xFFFF for c in p("a𝒳b😀c")["text"]) and p("a𝒳b😀c")["text"] == "abc")
long_text = "这是第一句话。" * 12 + "这是第二句，还没有句号" * 10  # > 150
r = p(json.dumps({"text": long_text, "face": ["neutral"]}, ensure_ascii=False))
check("超长: 截到 150 字以内", len(r["text"]) <= 150, str(len(r["text"])))
check("超长: 在最后一个句末标点处截断", r["text"].endswith("。") and r["text"] == long_text[:150][: long_text[:150].rfind("。") + 1], r["text"][-12:])
no_punct = "啊" * 400
check("超长且无句末标点: 硬截到 150 字", p(json.dumps({"text": no_punct}))["text"] == "啊" * 150)
check("恰好 150 字不截", len(p(json.dumps({"text": "啊" * 150}))["text"]) == 150)
check("句末标点 ！？… 也算", p(json.dumps({"text": "一" * 100 + "！" + "二" * 100}, ensure_ascii=False))["text"] == "一" * 100 + "！")
check("英文 ! ? 也算", p(json.dumps({"text": "a" * 100 + "?" + "b" * 100}))["text"] == "a" * 100 + "?")
check("空 text -> None(bad_response)", p('{"text": "", "face": ["grin"]}') is None)
check("空白 text -> None", p('{"text": "   ", "face": ["grin"]}') is None)
check("text 不是字符串 -> None", p('{"text": 5, "face": ["grin"]}') is None and p('{"face": ["grin"]}') is None)
check("text 全是 emoji -> None", p('{"text": "😀😀", "face": ["grin"]}') is None)
check("整段内容为空 -> None", p("") is None and p("   ") is None and p(None) is None)
check("reasoning_content 等字段被忽略: 取 choices[0].message.content",
      ac.extract_content(completion("正文")) == "正文")
check("extract_content 对坏结构返回 None", all(ac.extract_content(x) is None for x in (b"x", b"{}", b'{"choices":[]}', b'{"choices":[{"message":{"content":5}}]}')))

# end to end: the provider's reply goes through parse_reply
REPLY["content"] = '```json\n{"text": "端到端。", "face": ["wink", "bogus"]}\n```'
s = make()
s.ask("x")
check("端到端: replied 收到解析后的 dict", s.got == [{"text": "端到端。", "face": ["wink", "neutral"]}], str(s.got))

# ======================================================================= 6
print("== 6 错误分类 ==")
REPLY["content"] = '{"text": "好。", "face": ["neutral"]}'
for prefix, expect in (("/v1/e401", "auth"), ("/v1/e403", "auth"), ("/v1/e429", "rate_limit"), ("/v1/e500", "server"),
                       ("/v1/nonjson", "bad_response"), ("/v1/nochoices", "bad_response"), ("/v1/emptychoices", "bad_response"),
                       ("/v1/e404", "bad_response"), ("/v1/redirect", "bad_response")):
    s = make(prefix=prefix)
    s.ask("x")
    check(f"{prefix.split('/')[-1]} -> {expect}", s.errs == [expect] and s.got == [], str(s.errs))
REQS.clear()
s = make(prefix="/v1/redirect")
s.ask("x")
check("重定向不被跟随(服务器只看到 1 次请求, key 不会被带到别处)", [r["path"] for r in REQS] == ["/v1/redirect/chat/completions"], str([r["path"] for r in REQS]))
s = make(settings={"base_url": "http://127.0.0.1:1/v1"})
s.ask("x")
check("连不上的端口 -> network", s.errs == ["network"], str(s.errs))
s = make(prefix="/v1/stall", timeout=0.5)
t0 = time.monotonic()
s.ask("x")
check("超时(假服务器故意不回) -> network", s.errs == ["network"] and time.monotonic() - t0 < 1.9, str(s.errs))
for field in ("base_url", "model", "api_key"):
    for blank in ("", "   ", None):
        s = make(settings={field: blank})
        REQS.clear()
        s.ask("x")
        check(f"{field}={blank!r} -> no_config 且不发请求", s.errs == ["no_config"] and REQS == [], str(s.errs))
s = make(persona_path=TMP / "nope.md")
REQS.clear()
s.ask("x")
check("persona 文件不存在 -> no_persona 且不抛异常", s.errs == ["no_persona"] and REQS == [], str(s.errs))
empty = TMP / "empty.md"
empty.write_text("  \n", encoding="utf-8")
s = make(persona_path=empty)
s.ask("x")
check("persona 文件是空的 -> no_persona", s.errs == ["no_persona"])
s = make(substitute=lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
s.ask("x")
check("substitute 抛异常: 用原文继续, 不失败", s.got and not s.errs)
s = make(fetcher=lambda *a: (_ for _ in ()).throw(ValueError("weird")))
s.ask("x")
check("fetcher 抛了意外异常 -> network, 不抛", s.errs == ["network"])
s = make(prefix="/v1/e500")
s.ask("x")
check("失败后 busy 复位, 可以再问", s.busy is False and s.ask("y") is True)
s = make(prefix="/v1/e401", use_thread=True)
s.ask("x")
check("线程模式: 失败经信号回到主线程(auth)", wait_for(lambda: s.errs == ["auth"]), str(s.errs))

# ======================================================================= 7
print("== 7 https 校验 ==")
check("生产默认 validator 接受 https://", ac.default_url_validator("https://api.deepseek.com/chat/completions") is None)
for bad in ("http://api.deepseek.com/chat/completions", "http://127.0.0.1:9/x", "ftp://a.example/x", "file:///C:/x", "https:///x", "https://user:pw@a.example/x", "//a.example/x"):
    try:
        ac.default_url_validator(bad)
        ok = False
    except ac.ApiChatError as e:
        ok = e.code == "no_config"
    check(f"生产默认 validator 拒绝 {bad}", ok)
s = make(settings={"base_url": BASE + "/v1/ok"}, url_validator=ac.default_url_validator)
REQS.clear()
s.ask("x")
check("ChatSession 默认 validator: http:// 地址 -> no_config 且不发请求", s.errs == ["no_config"] and REQS == [], str(s.errs))
import inspect  # noqa: E402

check("ChatSession 构造默认就是 https-only 的 default_url_validator",
      inspect.signature(ac.ChatSession.__init__).parameters["url_validator"].default is ac.default_url_validator)

# ======================================================================= 8
print("== 8 日志不泄漏 ==")
app_log.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
if app_log.LOG_PATH.exists():
    app_log.LOG_PATH.unlink()
ERR_LOG = error_log.LOG_PATH
for prefix in ("/v1/e401", "/v1/e403", "/v1/e429", "/v1/e500", "/v1/nonjson", "/v1/nochoices", "/v1/redirect", "/v1/e404"):
    s = make(prefix=prefix, settings={"api_key": KEY})
    s.ask(USER_SECRET)
s = make(settings={"base_url": "http://127.0.0.1:1/v1"})
s.ask(USER_SECRET)
s = make(prefix="/v1/stall", timeout=0.3)
s.ask(USER_SECRET)
s = make(settings={"model": ""})
s.ask(USER_SECRET)
s = make(persona_path=TMP / "nope.md")
s.ask(USER_SECRET)
s = make(url_validator=ac.default_url_validator)
s.ask(USER_SECRET)
s = make(prefix="/v1/ok")
REPLY["content"] = '{"text": "ok。", "face": ["neutral"]}'
s.ask(USER_SECRET)
s.comment("机密应用名")
s = make(fetcher=lambda *a: (_ for _ in ()).throw(RuntimeError(f"leaky {KEY} {USER_SECRET}")))
s.ask(USER_SECRET)
all_logs = [p for p in TMP.rglob("*") if p.is_file() and p.suffix in (".log", ".txt")]
text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in all_logs)
check("app.log 里有失败记录(测试不是空转)", app_log.LOG_PATH.exists() and "API 聊天失败" in app_log.LOG_PATH.read_text(encoding="utf-8"))
log_text = app_log.LOG_PATH.read_text(encoding="utf-8")
check("日志里有错误分类与 HTTP 状态码", "auth" in log_text and "HTTP 401" in log_text and "rate_limit" in log_text and "HTTP 500" in log_text)
check("日志里有异常类型名", "URLError" in log_text or "timeout" in log_text.lower() or "RuntimeError" in log_text)
check("所有日志里都没有 key 哨兵", KEY not in text)
check("所有日志里都没有用户输入哨兵", USER_SECRET not in text)
check("日志里没有 Bearer / Authorization", "Bearer" not in text and "Authorization" not in text)
check("日志里没有回复正文 / 人设正文", "IGNORED-THINKING" not in text and "PERSONA-START" not in text and "ok。" not in text)
check("日志里没有 comment 的应用名", "机密应用名" not in text)
check("日志里没有完整 URL(没有 /chat/completions 或 ://)", "/chat/completions" not in log_text and "http://" not in log_text and "https://" not in log_text)
check("日志里 host 只到主机名", "host=127.0.0.1" in log_text)
check("fetcher 抛出含 key 的异常: 日志只有类型名", "RuntimeError" in log_text and "leaky" not in log_text)
check("没有写 error.log(没有未处理异常)", not ERR_LOG.exists() or KEY not in ERR_LOG.read_text(encoding="utf-8"))

# ======================================================================= 9
print("== 9 persona.md 进发布包 ==")
spec_text = (ROOT / "FloweyPet.spec").read_text(encoding="utf-8")
rel_text = (ROOT / "tools" / "build_release.py").read_text(encoding="utf-8")
check("FloweyPet.spec 的 datas 含 persona.md", "('persona.md', '.')" in spec_text)
check("build_release.py 的 REQUIRED 含 persona.md", '"persona.md"' in rel_text)
import paths  # noqa: E402

check("paths.PERSONA_PATH 指向 APP_DIR/persona.md", paths.PERSONA_PATH == paths.APP_DIR / "persona.md" and paths.PERSONA_PATH.exists())
check("模块只用标准库 + PyQt6 + 项目内模块(不 import pet_window / dialogue_box)",
      "pet_window" not in (ROOT / "src" / "api_chat.py").read_text(encoding="utf-8").replace("pet_window / dialogue_box", "")
      and "import dialogue_box" not in (ROOT / "src" / "api_chat.py").read_text(encoding="utf-8"))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("API 聊天模块用例全部通过")
sys.exit(0)
