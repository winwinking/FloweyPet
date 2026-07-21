# -*- coding: utf-8 -*-
"""反作弊：连点器(CPS)检测 + 存档篡改(total_clicks 被手动改)检测。

两个互相独立的类，都不依赖 Qt/GUI，纯逻辑，方便离线测试：

* AutoClickerDetector -- 每次点击喂一个时间戳，判断当前是否处于"连点器
  发作中"，返回该播哪一级台词(1-6，对应 AC-01~AC-06)。阶梯的时长门槛严
  格按用户文档：一旦点击速率(最近1秒内的点击数)达到"可疑"门槛并持续维
  持，从检测到这一刻开始计时，累计时长跨过 3/5/10/15/20/30 秒这几个门
  槛时依次播放阶段1~6；到阶段6后进入30秒完全无响应的冷却，冷却结束后如
  果再次被判定为连点器，直接从阶段3开始(跳过前两个"客气"阶段)——这个
  "跳过"标记只在本次进程运行期间记住，重启程序后重置。速率掉回正常且
  维持一小段时间，则整个发作过程清零，回到阶段0。

* SaveTamperGuard -- total_clicks 存在 config.json 里，用户能直接打开
  文件手改。检测手段是在 Windows 注册表里另外维护一份"影子"副本，只有
  程序自己的合法点击路径会同步更新两边；启动时两边一旦对不上，就说明
  config.json 被外部改过。check() 只负责读取+分类，不修改任何东西；
  resolve() 才真正把 total_clicks 纠正回影子值(不管上面判定成哪一类、
  播不播台词，篡改的数字永远留不住)。tamper_detect_count/tamper_locked/
  neg_tamper_count 三个字段持久化在 config.json 里(见 config.py 的
  DEFAULTS)，因为"反复篡改"这件事天然跨越多次启动(改文件必须先关闭
  程序)，不可能只发生在单次运行内。

  分类结果里 exempt=True 的两种(数值恰好是既有的里程碑/1000000+ 数字，
  或者恰好被改成0)是用户原始文档里明确标了"彩蛋"的两类特殊情况：不计
  入 tamper_detect_count，即使在锁定状态下也照常触发对应台词，而不是被
  锁定压成"……"/"……滚"。其余所有类别(A/B/C/D/E、负数四档、超长数字五
  档、组合彩蛋)一旦 tamper_locked 为真，一律改播极简回应池，且这个极简
  回应本身每次进程运行只播一次(见 SaveTamperGuard.locked_reply_allowed_
  this_run)。
"""
from time import monotonic

SAFE_DISPLAY_DIGITS = 12  # 超过这个位数就不再把原始数字打进台词文本

MILESTONE_THRESHOLDS = (100, 500, 1000, 2000, 5000, 10000, 20000, 50000)
CHEAT_MILESTONE_THRESHOLD = 1_000_000
SPECIAL_INTEGERS = (123456, 666666, 888888, 999999)

DELTA_MOD_A_MIN = 1000
DELTA_MOD_B_MIN = 10000
MOD_E_THRESHOLD = 3  # 第几次检测到篡改起，A/B/C/D 一律改播"反复修改"(MOD-E)
LOCK_THRESHOLD = 5  # tamper_detect_count 超过这个数就永久锁定


def digit_count(n):
    return len(str(abs(int(n))))


def format_number_for_display(n):
    """超过 SAFE_DISPLAY_DIGITS 位就不读了，直接用问号占位——用户实测
    过巨长数字(几百/几千/上万位)会被台词框的自动分页拆成几十页，这是那
    个 bug 的直接修复点。位数本身(digit_count)不受这个限制，永远可以
    正常显示，因为哪怕是"3000位"这种描述也只是个位数很少的小整数。"""
    if digit_count(n) > SAFE_DISPLAY_DIGITS:
        return "？？？？"
    return str(int(n))


class TamperVerdict:
    def __init__(self, kind, real_value, tampered_value, exempt=False, placeholders=None):
        self.kind = kind  # "mod_a".."mod_f" / "neg1".."neg4" / "long1".."long5" /
        # "combo1"/"combo2" / "existing_milestone"
        self.real_value = real_value
        self.tampered_value = tampered_value
        self.exempt = exempt
        self.placeholders = placeholders or {}


class AutoClickerDetector:
    SUSPICIOUS_RATE = 10  # 次/秒，可疑门槛
    CONFIRMED_RATE = 15  # 次/秒，确认门槛(目前仅用于文档语义，阶梯本身按时长推进)
    WINDOW_SECONDS = 1.0  # 计算"最近1秒点击数"的窗口
    RECOVERY_GRACE_SECONDS = 2.0  # 速率掉回可疑门槛以下且维持这么久，才清零发作状态
    COOLDOWN_SECONDS = 30.0  # AC-06 后的完全无响应冷却
    SKIP_TO_STAGE = 3  # 本次运行内已完整走过一轮后，下次发作直接从这一阶开始
    STAGE_THRESHOLDS = ((3, 1), (5, 2), (10, 3), (15, 4), (20, 5), (30, 6))

    def __init__(self, now_fn=monotonic):
        self._now = now_fn
        self._timestamps = []
        self._episode_start = None
        self._last_qualifying_click = None
        self._stage = 0
        self._cooldown_until = None
        self._has_completed_once = False

    def is_cooling_down(self):
        return self._cooldown_until is not None and self._now() < self._cooldown_until

    def on_click(self):
        """记一次点击时间戳，返回这次点击应该播放的阶段号(1-6)，或者
        None表示不用出声(还没到下一个门槛，或者已经播过当前门槛了)。
        调用方应先检查 is_cooling_down()，冷却期间根本不要调用这个方法
        (对应AC-06"无论怎么点都不再有任何反应")。"""
        now = self._now()
        self._timestamps = [t for t in self._timestamps if now - t < self.WINDOW_SECONDS]
        self._timestamps.append(now)
        rate = len(self._timestamps)

        if rate >= self.SUSPICIOUS_RATE:
            if self._episode_start is None:
                self._episode_start = now
            self._last_qualifying_click = now
        elif self._episode_start is not None:
            if now - self._last_qualifying_click >= self.RECOVERY_GRACE_SECONDS:
                self._episode_start = None
                self._stage = 0
            return None
        else:
            return None

        elapsed = now - self._episode_start
        target_stage = 0
        for threshold, stage in self.STAGE_THRESHOLDS:
            if elapsed >= threshold:
                target_stage = stage

        if self._has_completed_once and target_stage < self.SKIP_TO_STAGE:
            target_stage = 0

        if target_stage <= self._stage:
            return None

        self._stage = target_stage
        if target_stage == 6:
            self._cooldown_until = now + self.COOLDOWN_SECONDS
            self._has_completed_once = True
            self._episode_start = None
        return target_stage


class SaveTamperGuard:
    def __init__(self, read_shadow, write_shadow):
        """read_shadow()->Optional[int] / write_shadow(int) 由调用方注入
        (真实实现是 config.py 的 read_shadow_clicks/write_shadow_clicks，
        写进注册表；测试用一个内存里的假实现替换，避免测试真的去改机器
        全局共享的注册表项)。"""
        self._read_shadow = read_shadow
        self._write_shadow = write_shadow
        self._notified_locked_this_run = False

    def check(self, config):
        """启动时调用一次。返回 TamperVerdict，或者 None(没有篡改，包
        括从未建立过影子值的全新安装——这种情况下会顺手把当前值当作基
        准写进影子副本，不判定为篡改)。不修改 config 的 total_clicks，
        分类用到的计数字段(tamper_detect_count等)会被更新，但要不要真
        的采纳这次判定、要不要真的落盘，由调用方决定后调用 resolve()。"""
        shadow = self._read_shadow()
        current = config.get("total_clicks", 0)
        if shadow is None:
            self._write_shadow(current)
            return None
        if shadow == current:
            return None
        return self._classify(config, real_value=shadow, tampered_value=current)

    def _classify(self, config, real_value, tampered_value):
        if tampered_value == 0:
            return TamperVerdict("mod_f", real_value, tampered_value, exempt=True)
        # 复用既有的"1,000,000+作弊彩蛋"仅限于digit_count<10(9位以内，最高
        # 999,999,999)——再往上(10位起)交给下面的long1~5分级，否则任何巨
        # 长数字都会被这条过宽的">=1,000,000"提前拦截，永远走不到long分类，
        # 也就绕过了？？？？的安全显示(既有彩蛋台词直接用{clicks}原样打印
        # 数字，扛不住巨长数字)。
        if tampered_value in MILESTONE_THRESHOLDS or (
            CHEAT_MILESTONE_THRESHOLD <= tampered_value < 10 ** 9
        ):
            return TamperVerdict("existing_milestone", real_value, tampered_value, exempt=True)

        digits = digit_count(tampered_value)
        is_negative = tampered_value < 0
        is_long = digits >= 10

        if is_negative and is_long:
            kind = "combo1"
        elif is_negative:
            if tampered_value == -1:
                kind = "neg3"
            elif digits >= 8:
                kind = "neg4"
            else:
                prior = config.get("neg_tamper_count", 0)
                kind = "neg1" if prior == 0 else "neg2"
            config["neg_tamper_count"] = config.get("neg_tamper_count", 0) + 1
        elif is_long:
            if digits <= 12:
                kind = "long1"
            elif digits <= 15:
                kind = "long2"
            elif digits <= 20:
                kind = "long3"
            elif digits < 30:
                kind = "long4"
            else:
                kind = "long5"
        elif tampered_value in SPECIAL_INTEGERS:
            kind = "mod_c"
        else:
            delta = tampered_value - real_value
            if delta < 0:
                kind = "mod_d"
            elif delta >= DELTA_MOD_B_MIN:
                kind = "mod_b"
            elif delta >= DELTA_MOD_A_MIN:
                kind = "mod_a"
            else:
                return None  # 差值太小(<1000)，不当作篡改

        config["tamper_detect_count"] = config.get("tamper_detect_count", 0) + 1
        count = config["tamper_detect_count"]
        if kind in ("mod_a", "mod_b", "mod_c", "mod_d") and count >= MOD_E_THRESHOLD:
            kind = "mod_e"
        if count > LOCK_THRESHOLD:
            config["tamper_locked"] = True

        placeholders = {
            "real_clicks": format_number_for_display(real_value),
            "tampered_clicks": format_number_for_display(tampered_value),
            "tamper_n": str(count),
            "digit_count": str(digits),
        }
        return TamperVerdict(kind, real_value, tampered_value, exempt=False, placeholders=placeholders)

    def resolve(self, config, verdict):
        """无条件把 total_clicks 纠正回真实值并同步影子副本——不管上面
        判定成哪一类、播不播台词，篡改的数字永远留不住，只有真实点击才
        能再让它往上走。"""
        config["total_clicks"] = verdict.real_value
        self._write_shadow(verdict.real_value)

    def locked_reply_allowed_this_run(self):
        """锁定状态下的极简"……"/"……滚"回应，每次进程运行最多播一次；
        再往后的篡改尝试(同一次运行内)彻底不再触发任何台词。"""
        if self._notified_locked_this_run:
            return False
        self._notified_locked_this_run = True
        return True
