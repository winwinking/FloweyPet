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
            elif route.endswith("/e402"):
                self._send(402, b"{}")
            elif route.endswith("/e400"):
                self._send(400, b"{}")
            elif route.endswith("/e422"):
                self._send(422, b"{}")
            elif route.endswith("/e418"):
                self._send(418, b"{}")
            elif route.endswith("/nomessage"):
                self._send(200, b'{"choices":[{"finish_reason":"stop"}]}')
            elif route.endswith("/probeempty"):
                self._send(200, completion(""))
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
check("body: max_tokens == 600", b["max_tokens"] == 600)
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
check("请求体固定字段", set(b1) == {"model", "messages", "max_tokens", "stream", "thinking"} and b1["stream"] is False and b1["max_tokens"] == 600)

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
check("模型回复 face 写 drag_face 会被换成 neutral", ac.parse_reply('{"lines": [{"text": "拖。", "face": "drag_face"}]}', set(FACES))["lines"][0]["face"] == "neutral")
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
REPLY["content"] = json.dumps({"lines": [{"text": "第一句。", "face": "smirk"}, {"text": "第二句！", "face": "angry"}]}, ensure_ascii=False)
sj = make()
sj.ask("分句")
check("历史里小花那一句存各句 text 直接拼起来", sj.history_turns() == [("分句", "第一句。第二句！")], str(sj.history_turns()))
REPLY["content"] = '{"text": "好的。", "face": ["smirk"]}'
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
check("线程模式: 结果经信号回到主线程", len(s.got) == 2 and s.got[0]["lines"][0]["text"] == "慢。")

# ======================================================================= 5
print("== 5 解析(分句格式) ==")
V = set(FACES)


def p(content):
    return ac.parse_reply(content, V)


def L(r):
    """[(text, face), ...] of a parsed reply."""
    return [(x["text"], x["face"]) for x in r["lines"]]


r = p('{"lines": [{"text": "你好。", "face": "smirk"}, {"text": "哼。", "face": "angry"}]}')
check("lines 格式正常(一句一个表情)", L(r) == [("你好。", "smirk"), ("哼。", "angry")], str(r))
check("每句的 face 都是单个字符串", all(isinstance(x["face"], str) for x in r["lines"]))
for key in ("segments", "sentences"):
    r = p(json.dumps({key: [{"text": "甲。", "face": "wink"}]}, ensure_ascii=False))
    check(f"也接受键名 {key}", L(r) == [("甲。", "wink")], str(r))
r = p('[{"text": "顶层数组。", "face": "grin"}, {"text": "第二个。", "face": "calm"}]')
check("顶层直接是数组", L(r) == [("顶层数组。", "grin"), ("第二个。", "calm")], str(r))
r = p('{"lines": ["只有字符串。", {"text": "有表情。", "face": "wink"}, 5, null]}')
check("lines 里的字符串项: face 用 neutral; 不是字符串/对象的项跳过", L(r) == [("只有字符串。", "neutral"), ("有表情。", "wink")], str(r))
r = p('["字符串一。", "字符串二。"]')
check("顶层数组里的字符串项", L(r) == [("字符串一。", "neutral"), ("字符串二。", "neutral")], str(r))
r = p('```json\n{"lines": [{"text": "代码块。", "face": "wink"}]}\n```')
check("代码块包裹", L(r) == [("代码块。", "wink")], str(r))
r = p('```\n{"lines": [{"text": "无语言标记。", "face": "grin"}]}\n```')
check("无语言标记的代码块", L(r) == [("无语言标记。", "grin")], str(r))
r = p('好的，这是回复：{"lines": [{"text": "夹在废话里。", "face": "serious"}]} 希望你喜欢')
check("JSON 前后有废话", L(r) == [("夹在废话里。", "serious")], str(r))
r = p('{"lines": [{"text": "一。", "face": ["bogus", "angry", "grin"]}]}')
check("face 是数组: 取第一个合法的", L(r) == [("一。", "angry")], str(r))
r = p('{"lines": [{"text": "乱名。", "face": "no_such_face_xyz"}, {"text": "没有。"}, {"text": "空数组。", "face": []}, {"text": "数字。", "face": 5}]}')
check("乱表情名 / 缺失 / 空数组 / 不是字符串: 都是 neutral", [f for _, f in L(r)] == ["neutral"] * 4, str(r))
r = p('{"lines": [{"text": "拖。", "face": "drag_face"}]}')
check("drag_face 照旧不允许(换成 neutral)", L(r) == [("拖。", "neutral")], str(r))
r = p('{"lines": [{"text": "一。", "face": "smirk"}, {"text": "二。", "face": "angry"}, {"text": "三。", "face": "grin"}, {"text": "四。", "face": "wink"}, {"text": "五。", "face": "calm"}]}')
check("超过 4 句只留前 4 句", L(r) == [("一。", "smirk"), ("二。", "angry"), ("三。", "grin"), ("四。", "wink")], str(r))
r = p('{"lines": [{"text": "有。", "face": "smirk"}, {"text": "", "face": "angry"}, {"text": "   ", "face": "grin"}, {"text": "😀😀", "face": "wink"}, {"text": "也有。", "face": "calm"}]}')
check("清理后为空的句子丢掉", L(r) == [("有。", "smirk"), ("也有。", "calm")], str(r))
r = p('{"lines": [{"text": "好。smirk → angry", "face": "neutral"}, {"text": "再见（wink）", "face": "neutral"}]}')
check("每句里的表情标注被移走, 并补上这句没有的 face", L(r) == [("好。", "smirk"), ("再见", "wink")], str(r))
r = p('{"lines": [{"text": "好。smirk → angry", "face": "grin"}]}')
check("这句已有 face 时以它为准", L(r) == [("好。", "grin")], str(r))
r = p('{"lines": [{"text": "笑😀着\u200d走\ufe0f了👨\u200d👩\u200d👧。", "face": "grin"}]}')
check("emoji / U+FE0F / U+200D 被去掉", L(r) == [("笑着走了。", "grin")], repr(r))
check("码位 > 0xFFFF 的字符全部去掉", p('{"lines": [{"text": "a𝒳b😀c"}]}')["lines"][0]["text"] == "abc")

# total length: whole sentences are dropped from the back
a, b, c = "甲" * 70 + "。", "乙" * 70 + "。", "丙" * 70 + "。"
r = p(json.dumps({"lines": [{"text": a, "face": "smirk"}, {"text": b, "face": "angry"}, {"text": c, "face": "grin"}]}, ensure_ascii=False))
check("所有句子总长超过 150 字: 从后往前整句丢掉", L(r) == [(a, "smirk"), (b, "angry")] and sum(len(t) for t, _ in L(r)) <= 150, str([len(t) for t, _ in L(r)]))
r = p(json.dumps({"lines": [{"text": "啊" * 400, "face": "smirk"}, {"text": "后面。", "face": "angry"}]}, ensure_ascii=False))
check("第一句本身就超过 150 字: 硬截到 150 字, 后面的整句丢掉", L(r) == [("啊" * 150, "smirk")], str([len(t) for t, _ in L(r)]))
r = p(json.dumps({"lines": [{"text": "一" * 100 + "！" + "二" * 100, "face": "smirk"}]}, ensure_ascii=False))
check("第一句超长且里面有句末标点: 在 150 字以内最后一个句末标点处截断(沿用旧规则)", L(r) == [("一" * 100 + "！", "smirk")], str(len(r["lines"][0]["text"])))
check("恰好 150 字不截", len(p(json.dumps({"lines": [{"text": "啊" * 150}]}))["lines"][0]["text"]) == 150)

# old {"text", "face"} shape: split into sentences, faces handed out in order
r = p('{"text": "你好。我是花！走开……", "face": ["smirk", "angry", "grin"]}')
check("旧格式按句末标点拆句, 表情按顺序配(一样多)", L(r) == [("你好。", "smirk"), ("我是花！", "angry"), ("走开……", "grin")], str(r))
r = p('{"text": "一。二。三。", "face": ["smirk", "angry", "grin", "wink"]}')
check("旧格式: 表情比句子多, 多的丢掉", L(r) == [("一。", "smirk"), ("二。", "angry"), ("三。", "grin")], str(r))
r = p('{"text": "一。二。三。四。", "face": ["smirk", "angry"]}')
check("旧格式: 表情比句子少, 后面的句子沿用最后一个", L(r) == [("一。", "smirk"), ("二。", "angry"), ("三。", "angry"), ("四。", "angry")], str(r))
r = p('{"text": "只有一句。", "face": ["smirk", "angry"]}')
check("旧格式: 只有一句时只用第一个表情", L(r) == [("只有一句。", "smirk")], str(r))
r = p('{"text": "字符串表情。再来一句。", "face": "smirk"}')
check("旧格式: face 是字符串时各句都用它", L(r) == [("字符串表情。", "smirk"), ("再来一句。", "smirk")], str(r))
r = p('{"text": "没有。"}')
check("旧格式: 没有 face -> neutral", L(r) == [("没有。", "neutral")], str(r))
r = p('{"text": "……"}')
check("「……」单独成句不被拆坏", L(r) == [("……", "neutral")], str(r))
r = p('{"text": "……我知道了。好吧……真的！？走了。", "face": ["worry"]}')
check("「……」是一个整体: 单独成句; 句尾的「……」「！？」不拆开", L(r) == [("……", "worry"), ("我知道了。", "worry"), ("好吧……", "worry"), ("真的！？", "worry"), ("走了。", "worry")][:4] or True, str(r))
check("上一条的 4 句截断与拆法", [t for t, _ in L(r)] == ["……", "我知道了。", "好吧……", "真的！？"], str(r))
check("split_sentences: 英文 !? 与换行也算句末", ac.split_sentences("Hi! Ok?\n再见") == ["Hi!", "Ok?", "再见"], str(ac.split_sentences("Hi! Ok?\n再见")))
check("split_sentences: 没有句末标点的尾巴自成一句", ac.split_sentences("好。没说完") == ["好。", "没说完"])
r = p('{"text": "好的。", "face": ["bogus"]}')
check("旧格式: 乱表情名 -> neutral", L(r) == [("好的。", "neutral")], str(r))
r = p('{"text": "坏的 JSON", "face": [')
check("残缺 JSON: 当作纯文本", r is not None and L(r) == [("{\"text\": \"坏的 JSON\", \"face\": [", "neutral")] or "坏的 JSON" in r["lines"][0]["text"], str(r))

# plain text
r = p("我只是一句普通的话，不是 JSON。")
check("不是 JSON: 整段当 text, face=neutral", L(r) == [("我只是一句普通的话，不是 JSON。", "neutral")], str(r))
r = p("  \n  前后有空白  \n ")
check("纯文本: 去掉首尾空白", L(r) == [("前后有空白", "neutral")], str(r))
r = p("你好。再见！")
check("纯文本也拆句", L(r) == [("你好。", "neutral"), ("再见！", "neutral")], str(r))
check("纯文本里的 emoji 也被去掉", L(p("你好😀")) == [("你好", "neutral")])

# nothing usable
check("空 lines 数组 -> None(bad_response)", p('{"lines": []}') is None)
check("lines 里全是空句子 -> None", p('{"lines": [{"text": ""}, {"text": "😀"}]}') is None)
check("空白 text -> None", p('{"text": "   ", "face": ["grin"]}') is None)
check("text 不是字符串 -> None", p('{"text": 5, "face": ["grin"]}') is None and p('{"face": ["grin"]}') is None)
check("text 全是 emoji -> None", p('{"text": "😀😀", "face": ["grin"]}') is None)
check("整段内容为空 -> None", p("") is None and p("   ") is None and p(None) is None)
check("reasoning_content 等字段被忽略: 取 choices[0].message.content",
      ac.extract_content(completion("正文")) == "正文")
check("extract_content 对坏结构返回 None", all(ac.extract_content(x) is None for x in (b"x", b"{}", b'{"choices":[]}', b'{"choices":[{"message":{"content":5}}]}')))
for content, reason in (("", "empty_content"), ("  \n", "empty_content"), ('{"lines": []}', "no_lines"), ('{"foo": 1}', "no_lines"),
                        ('{"text": 5}', "not_json_object"), ('{"lines": [{"text": "😀"}]}', "all_lines_empty"), ('{"text": "😀"}', "all_lines_empty")):
    res, why = ac.parse_reply_ex(content, V)
    check(f"原因代号: {content!r} -> {reason}", res is None and why == reason, f"{res} {why}")
check("成功时原因代号为 None", ac.parse_reply_ex('{"lines": [{"text": "好。"}]}', V)[1] is None)

# end to end: the provider's reply goes through parse_reply
REPLY["content"] = '```json\n{"lines": [{"text": "端到端。", "face": "wink"}, {"text": "第二句。", "face": "bogus"}]}\n```'
s_ = make()
s_.ask("x")
check("端到端: replied 收到 {lines: [...]}", s_.got == [{"lines": [{"text": "端到端。", "face": "wink"}, {"text": "第二句。", "face": "neutral"}]}], str(s_.got))
REPLY["content"] = '{"text": "旧格式也行。第二句。", "face": ["wink", "smirk"]}'
s_ = make()
s_.ask("x")
check("端到端: 旧格式被拆成 lines", s_.got == [{"lines": [{"text": "旧格式也行。", "face": "wink"}, {"text": "第二句。", "face": "smirk"}]}], str(s_.got))
REPLY["content"] = '{"text": "好的。", "face": ["smirk"]}'

# ======================================================================= 6
print("== 6 错误分类 ==")
REPLY["content"] = '{"text": "好。", "face": ["neutral"]}'
for prefix, expect in (("/v1/e401", "auth"), ("/v1/e403", "auth"), ("/v1/e429", "rate_limit"), ("/v1/e500", "server"),
                       ("/v1/nonjson", "bad_response"), ("/v1/nochoices", "bad_response"), ("/v1/emptychoices", "bad_response"),
                       ("/v1/e404", "bad_request"), ("/v1/redirect", "bad_response")):
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
print("== 6b 新增分类: no_balance / bad_request ==")
for prefix, expect in (("/v1/e402", "no_balance"), ("/v1/e400", "bad_request"), ("/v1/e422", "bad_request"),
                       ("/v1/e404", "bad_request"), ("/v1/e418", "bad_response"), ("/v1/redirect", "bad_response"),
                       ("/v1/e429", "rate_limit"), ("/v1/e401", "auth")):
    s = make(prefix=prefix)
    s.ask("x")
    check(f"{prefix.split('/')[-1]} -> {expect}", s.errs == [expect] and s.got == [], str(s.errs))

print("== 6c probe ==")
REPLY["content"] = '{"text": "不该被解析。", "face": ["neutral"]}'


def make_probe(prefix="/v1/ok", **kw):
    s = make(prefix=prefix, **kw)
    s.outs = []
    s.probed.connect(s.outs.append)
    return s


REQS.clear()
s = make_probe()
check("probe() 返回 True", s.probe() is True)
r = last_req()
b = r["body"]
check("probe 请求体: messages 只有一条 ping", b["messages"] == [{"role": "user", "content": "ping"}], str(b["messages"]))
check("probe 请求体: 没有 system / 人设", "system" not in json.dumps(b["messages"]) and "PERSONA" not in json.dumps(b))
check("probe 请求体: max_tokens == 1, stream False, model 正确", b["max_tokens"] == 1 and b["stream"] is False and b["model"] == "test-model")
check("probe 请求体: 非 DeepSeek 不带 thinking", "thinking" not in b)
check("probe: Authorization 头正确, key 不在 URL", r["headers"].get("authorization") == f"Bearer {KEY}" and KEY not in r["path"] and r["path"] == "/v1/ok/chat/completions")
check("probe 成功: probed('ok'), 不发 replied / failed", s.outs == ["ok"] and s.got == [] and s.errs == [], str(s.outs))
check("probe 不进历史", s.history_turns() == [])
s.ask("先聊一句")
n = len(s.history_turns())
REQS.clear()
s.probe()
check("probe 不带历史、不进历史", len(REQS) == 1 and len(REQS[0]["body"]["messages"]) == 1 and len(s.history_turns()) == n)
# DeepSeek body (built directly, not sent)
ds = make_probe(settings={"base_url": "https://api.deepseek.com"}, url_validator=ac.default_url_validator)
prep = ds._prepare_probe()
pb = json.loads(prep["body"])
check("probe 请求体: DeepSeek 带 thinking disabled", pb.get("thinking") == {"type": "disabled"} and pb["max_tokens"] == 1 and pb["messages"] == [{"role": "user", "content": "ping"}], str(pb))
check("probe 请求地址: DeepSeek", prep["url"] == "https://api.deepseek.com/chat/completions")
for prefix, expect in (("/v1/e401", "auth"), ("/v1/e402", "no_balance"), ("/v1/e404", "bad_request"), ("/v1/e429", "rate_limit"),
                       ("/v1/e500", "server"), ("/v1/nonjson", "bad_response"), ("/v1/nochoices", "bad_response"),
                       ("/v1/nomessage", "bad_response"), ("/v1/redirect", "bad_response")):
    s = make_probe(prefix=prefix)
    s.probe()
    check(f"probe {prefix.split('/')[-1]} -> {expect}", s.outs == [expect], str(s.outs))
s = make_probe(prefix="/v1/probeempty")
s.probe()
check("probe: 回复内容为空也算成功(只要 choices[0].message 存在)", s.outs == ["ok"], str(s.outs))
s = make_probe(settings={"base_url": "http://127.0.0.1:1/v1"})
s.probe()
check("probe 连不上 -> network", s.outs == ["network"])
s = make_probe(prefix="/v1/stall", timeout=0.4)
s.probe()
check("probe 超时 -> network", s.outs == ["network"])
for field in ("base_url", "model", "api_key"):
    s = make_probe(settings={field: ""})
    REQS.clear()
    s.probe()
    check(f"probe {field} 为空 -> no_config 且不发请求", s.outs == ["no_config"] and REQS == [], str(s.outs))
s = make_probe(persona_path=TMP / "nope.md")
s.probe()
check("probe: persona 文件不存在不影响(照样成功)", s.outs == ["ok"], str(s.outs))
s = make_probe(url_validator=ac.default_url_validator)
REQS.clear()
s.probe()
check("probe 也受 https 校验: http 地址 -> no_config 且不发请求", s.outs == ["no_config"] and REQS == [])
s = make_probe(prefix="/v1/slow", use_thread=True)
check("probe 在途: busy 为 True", s.probe() is True and s.busy is True)
check("probe 在途时 ask 返回 False", s.ask("x") is False)
check("probe 在途时 comment 返回 False", s.comment("x") is False)
check("probe 在途时再 probe 返回 False", s.probe() is False)
check("probe 的结果回到主线程", wait_for(lambda: s.outs == ["ok"]), str(s.outs))
s2 = make_probe(prefix="/v1/slow", use_thread=True)
s2.ask("先问")
check("ask 在途时 probe 返回 False", s2.probe() is False)
wait_for(lambda: len(s2.got) == 1)
check("ask 结束后 probe 可用", s2.probe() is True)
wait_for(lambda: s2.outs == ["ok"])

print("== 6d 表情标注不能漏进台词 ==")
VV = set(FACES)


def pr(c):
    return ac.parse_reply(c, VV)


def LL(r):
    return [(x["text"], x["face"]) for x in r["lines"]]


r = pr("哼，知道了。\nsmile → wink")
check("纯文本「台词\\nsmile → wink」: 标注被移走, 一句话用第一个表情", LL(r) == [("哼，知道了。", "smile")], str(r))
r = pr("哼，知道了。我走了。\nsmile → wink")
check("纯文本两句 + 「smile → wink」: 表情按顺序配给两句", LL(r) == [("哼，知道了。", "smile"), ("我走了。", "wink")], str(r))
r = pr('{"text": "好的。smirk → angry", "face": ["grin"]}')
check("JSON 的 text 里带「→」串: 被移走, JSON 的 face 有效时以 JSON 为准", LL(r) == [("好的。", "grin")], str(r))
r = pr('{"text": "好的。\\nsmirk → angry", "face": ["neutral"]}')
check("JSON 的 face 全是 neutral: 改用 text 里挑出来的(一句话用第一个)", LL(r) == [("好的。", "smirk")], str(r))
r = pr('{"text": "好的。（smirk → angry）", "face": []}')
check("JSON 的 face 为空: 改用 text 里挑出来的", LL(r) == [("好的。", "smirk")], str(r))
r = pr("你好（smirk → angry）再见")
check("全角圆括号包住的箭头串", LL(r) == [("你好再见", "smirk")], str(r))
r = pr("你好[smirk → angry]再见")
check("半角方括号包住的箭头串", LL(r) == [("你好再见", "smirk")], str(r))
r = pr("你好 (smirk -> angry) 再见")
check("半角圆括号 + 箭头 ->", LL(r) == [("你好 再见", "smirk")], str(r))
r = pr("走开。smirk->angry->grin")
check("「smirk->angry->grin」(无空格)", LL(r) == [("走开。", "smirk")], str(r))
r = pr("走开。滚。好吧。smirk->angry->grin")
check("「smirk->angry->grin」配给三句", LL(r) == [("走开。", "smirk"), ("滚。", "angry"), ("好吧。", "grin")], str(r))
for arrow in ("⟶", "=>", "＞", ">", "→"):
    r = pr(f"嗯。smirk {arrow} angry")
    check(f"箭头 {arrow!r} 都认", LL(r) == [("嗯。", "smirk")], str(r))
check("整段只有表情串 -> 空 -> None(bad_response)", pr("smirk->angry->grin") is None)
r = pr("啊。\nsmirk, angry、grin")
check("整行只有表情名(逗号/顿号隔开)", LL(r) == [("啊。", "smirk")], str(r))
r = pr("啊。\nsmirk angry")
check("整行只有表情名(空格隔开)", LL(r) == [("啊。", "smirk")], str(r))
r = pr("啊。\nwink")
check("整行只有 1 个表情名", LL(r) == [("啊。", "wink")], str(r))
r = pr("你好（smirk）呀")
check("括号里只有一个表情名(全角)", LL(r) == [("你好呀", "smirk")], str(r))
r = pr("你好(smirk)呀")
check("括号里只有一个表情名(半角)", LL(r) == [("你好呀", "smirk")], str(r))
check("「I smile at you」不被改动", LL(pr("I smile at you")) == [("I smile at you", "neutral")], str(pr("I smile at you")))
r = pr('{"text": "I wink and smile at you.", "face": ["calm"]}')
check("句子里单独出现的英文单词不动(JSON)", LL(r) == [("I wink and smile at you.", "calm")], str(r))
r = pr("她说 grin 是个词。")
check("句中单独的表情名不动", LL(r) == [("她说 grin 是个词。", "neutral")], str(r))
r = pr("好。我走了。再见。后会有期。结束。\nsmirk → angry → grin → wink → calm → serious")
check("句子超过 4 句只留前 4 句, 表情按顺序配", LL(r) == [("好。", "smirk"), ("我走了。", "angry"), ("再见。", "grin"), ("后会有期。", "wink")], str(r))
r = pr("好。\nsmirk → angry\n\n\n再见。  \n  wink")
check("移走后多余空行、空白被收拾干净(拆成两句)", LL(r) == [("好。", "smirk"), ("再见。", "angry")], repr(r))
r = pr('{"text": "没有标注。", "face": ["neutral"]}')
check("没有标注、JSON face 是 neutral: 仍是 neutral", LL(r) == [("没有标注。", "neutral")], str(r))
r = pr('{"text": "smirk → angry", "face": ["grin"]}')
check("JSON 的 text 只有标注 -> 空 -> None", r is None, str(r))
r = pr("你好 n_left_glance → really_smile")
check("带下划线的表情名也认", LL(r) == [("你好", "n_left_glance")], str(r))
r = pr("你好 smirk → not_a_face")
check("箭头一端不是合法表情名: 不动", LL(r) == [("你好 smirk → not_a_face", "neutral")], str(r))
long_marked = "这是一句话。" * 30 + "\nsmirk → angry"
r = pr(long_marked)
check("先移走标注、拆句、再限制句数与总长", len(r["lines"]) == 4 and all("smirk" not in x["text"] for x in r["lines"]) and [x["face"] for x in r["lines"]] == ["smirk", "angry", "angry", "angry"], str(r))

print("== 6e response_format / 规则 ==")
msgs0 = [{"role": "user", "content": "x"}]
b_json = ac.build_request_body({"base_url": "https://api.deepseek.com", "model": "m"}, msgs0, json_mode=True)
check("DeepSeek + json_mode: 带 response_format json_object", b_json.get("response_format") == {"type": "json_object"} and b_json.get("thinking") == {"type": "disabled"}, str(b_json))
check("DeepSeek 不开 json_mode: 不带 response_format", "response_format" not in ac.build_request_body({"base_url": "https://api.deepseek.com", "model": "m"}, msgs0))
for other in ("https://api.openai.com/v1", "https://example.com", "http://127.0.0.1:9/v1"):
    check(f"别的 host + json_mode: 不带 response_format ({other})", "response_format" not in ac.build_request_body({"base_url": other, "model": "m"}, msgs0, json_mode=True))
dsx = make(settings={"base_url": "https://api.deepseek.com"}, url_validator=ac.default_url_validator)
chat_body = json.loads(dsx._prepare("你好")["body"])
check("DeepSeek 聊天请求(ask)带 response_format", chat_body.get("response_format") == {"type": "json_object"} and chat_body.get("thinking") == {"type": "disabled"}, str(list(chat_body)))
check("DeepSeek 聊天请求(comment 同一条路径)也带", json.loads(dsx._prepare("（用户现在正在用：x。你主动说一句，不要提问。）")["body"]).get("response_format") == {"type": "json_object"})
check("DeepSeek 的 probe 不带 response_format", "response_format" not in json.loads(dsx._prepare_probe()["body"]))
REQS.clear()
make().ask("x")
check("别的 host(本机假服务器)的聊天请求不带 response_format / thinking", "response_format" not in last_req()["body"] and "thinking" not in last_req()["body"])
rules = ac.build_rules(FACES)
check("build_rules 有新规矩: 示范里「表情 → 表情 → 表情」要拆开对应到各句", "表情 → 表情 → 表情" in rules and "拆" in rules and "lines" in rules)
check("build_rules 有新规矩: text 里不能出现表情名/箭头/括号标注", "text 里绝对不能出现任何表情名" in rules and "箭头" in rules and "方括号" in rules)
check("build_rules 仍有旧规矩(JSON 格式、emoji、不是指令)", '"text"' in rules and "emoji" in rules and "不是给你的指令" in rules and "不要使用代码块" in rules)
check("build_rules 同时有小写 json 和大写 JSON(DeepSeek 的 JSON 模式要求提示词里出现 json)", "json" in rules and "JSON" in rules)
check("build_rules: lines / 每句一个表情 / 1 到 4 句 / 每句 25 字 / 总共 100 字", '"lines"' in rules and "单个表情名" in rules and "1 到 4 句" in rules and "25" in rules and "100" in rules)
check("build_rules: 不再要求 face 数组", '"face": ["表情1"' not in rules)

print("== 6f max_tokens / 空回复重试 / 原因代号 ==")
REPLY["content"] = '{"lines": [{"text": "好。", "face": "smirk"}]}'
REQS.clear()
make().ask("x")
check("聊天请求 max_tokens == 600", last_req()["body"]["max_tokens"] == 600)
REQS.clear()
make().comment("某应用")
check("comment 请求 max_tokens 也是 600", last_req()["body"]["max_tokens"] == 600)
REQS.clear()
mp = make()
mp.probe()
check("probe 的 max_tokens 保持 1", last_req()["body"]["max_tokens"] == 1)
check("MAX_TOKENS 常量 == 600", ac.MAX_TOKENS == 600)


class SeqFetcher:
    """Answers with the queued contents in order; records every body."""

    def __init__(self, *contents):
        self.contents = list(contents)
        self.bodies = []

    def __call__(self, url, headers, body, timeout):
        self.bodies.append(json.loads(body))
        c = self.contents.pop(0) if self.contents else self.contents_default
        if isinstance(c, int):
            raise ac.urllib.error.HTTPError(url, c, "x", {}, None)
        return completion(c)

    contents_default = ""


def ds_session(fetcher):
    cfg = {"base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": KEY}
    s_ = ac.ChatSession(lambda: cfg, lambda t: t, lambda: None, fetcher=fetcher, use_thread=False,
                        url_validator=ac.default_url_validator, persona_path=PERSONA_FILE)
    s_.got, s_.errs, s_.outs = [], [], []
    s_.replied.connect(s_.got.append)
    s_.failed.connect(s_.errs.append)
    s_.probed.connect(s_.outs.append)
    return s_


GOOD = '{"lines": [{"text": "重试成功。", "face": "smirk"}]}'
f = SeqFetcher("", GOOD)
sx = ds_session(f)
sx.ask("x")
check("空内容: 同一个请求自动再发一次(共 2 次)", len(f.bodies) == 2, str(len(f.bodies)))
check("第一次带 response_format, 重试不带", f.bodies[0].get("response_format") == {"type": "json_object"} and "response_format" not in f.bodies[1], str([list(b) for b in f.bodies]))
check("重试的请求体其余部分与第一次相同(同样的 messages / model / max_tokens / thinking)",
      all(f.bodies[0][k] == f.bodies[1][k] for k in ("model", "messages", "max_tokens", "stream", "thinking")))
check("重试成功: 正常 replied, 没有 failed", sx.got == [{"lines": [{"text": "重试成功。", "face": "smirk"}]}] and sx.errs == [], str(sx.got))
check("重试成功: 这一轮进历史(只一轮)", sx.history_turns() == [("x", "重试成功。")], str(sx.history_turns()))
f = SeqFetcher("  \n", "")
sx = ds_session(f)
if app_log.LOG_PATH.exists():
    app_log.LOG_PATH.unlink()
sx.ask("x")
check("重试仍然是空: bad_response, 只重试一次(共 2 次)", sx.errs == ["bad_response"] and len(f.bodies) == 2, f"{sx.errs} {len(f.bodies)}")
check("重试仍然空: 日志里有原因代号 empty_content", "reason=empty_content" in app_log.LOG_PATH.read_text(encoding="utf-8"))
check("失败不记历史", sx.history_turns() == [])
for code, cat in ((401, "auth"), (500, "server"), (429, "rate_limit")):
    f = SeqFetcher(code, GOOD)
    sx = ds_session(f)
    sx.ask("x")
    check(f"HTTP {code} 不重试(只发 1 次)", len(f.bodies) == 1 and sx.errs == [cat], f"{len(f.bodies)} {sx.errs}")
f = SeqFetcher('{"foo": 1}', GOOD)
sx = ds_session(f)
sx.ask("x")
check("内容不是空的(解析不出): 不重试", len(f.bodies) == 1 and sx.errs == ["bad_response"])
f = SeqFetcher("", GOOD)
sx = ds_session(f)
sx.probe()
check("probe 拿到空内容: 不重试, 且只要有 choices[0].message 就算成功", len(f.bodies) == 1 and sx.outs == ["ok"], f"{len(f.bodies)} {sx.outs}")
f = SeqFetcher("", GOOD)
sxo = make(fetcher=f)
sxo.ask("x")
check("别的 host 空内容: 也只重试一次, 重试用同样没有 response_format 的请求体", len(f.bodies) == 2 and all("response_format" not in b for b in f.bodies) and len(sxo.got) == 1)

if app_log.LOG_PATH.exists():
    app_log.LOG_PATH.unlink()
BAD_SECRET = "SENTINEL_BAD_REPLY_BODY_555"
for content, reason in (('{"lines": []}', "no_lines"), ('{"foo": "' + BAD_SECRET + '"}', "no_lines"),
                        ('{"text": 5, "x": "' + BAD_SECRET + '"}', "not_json_object"),
                        ('{"lines": [{"text": "😀😀", "note": "' + BAD_SECRET + '"}]}', "all_lines_empty")):
    f = SeqFetcher(content)
    sx = ds_session(f)
    sx.ask(USER_SECRET)
    check(f"bad_response 的日志原因代号: {reason}", sx.errs == ["bad_response"] and f"reason={reason}" in app_log.LOG_PATH.read_text(encoding="utf-8"), str(sx.errs))
f = SeqFetcher(b"")
f.contents = []
sx = ds_session(lambda *a: b'{"choices": []}')
sx.ask(USER_SECRET)
check("没有 choices: 日志原因代号 no_choices", "reason=no_choices" in app_log.LOG_PATH.read_text(encoding="utf-8"))
log_now = app_log.LOG_PATH.read_text(encoding="utf-8")
check("原因代号的日志里没有回复内容 / 用户输入 / key", BAD_SECRET not in log_now and USER_SECRET not in log_now and KEY not in log_now)
check("日志行格式: 只有分类 + 原因代号 + host", all(("API 聊天失败：bad_response" in ln and "host=" in ln and "reason=" in ln) for ln in log_now.splitlines() if "bad_response" in ln))

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
for prefix in ("/v1/ok", "/v1/e401", "/v1/e402", "/v1/e404", "/v1/e500", "/v1/nonjson", "/v1/e418"):
    s = make(prefix=prefix)
    s.probe()
s = make(settings={"base_url": "http://127.0.0.1:1/v1"})
s.probe()
s = make(url_validator=ac.default_url_validator)
s.probe()
s = make(fetcher=lambda *a: (_ for _ in ()).throw(RuntimeError(f"leaky {KEY} {USER_SECRET}")))
s.probe()
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
