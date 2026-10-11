# -*- coding: utf-8 -*-
"""Update check: reads ONE public JSON file (version.json) and tells the
pet window if a newer version exists. It only ever downloads that small
file -- nothing about the user or the machine is sent (the request carries
just a User-Agent of "FloweyPet/<version>") -- and it never downloads or
replaces the program itself; the user clicks through to the Releases page.

Standard library only (urllib / json / threading). The network fetch runs
on a daemon thread; its result is handed back to the Qt main thread through
a signal. Every failure is silent: one line in app.log, no popup, no speech.
"""
import json
import re
import threading
import urllib.error
import urllib.request

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import app_log
import version
from paths import IS_DEV

DEFAULT_RELEASE_URL = "https://github.com/winwinking/FloweyPet/releases/latest"
ALLOWED_URL_PREFIX = "https://github.com/winwinking/FloweyPet/"
MAX_READ_BYTES = 64 * 1024
MAX_LINE_CHARS = 200
ENABLE_RECHECK_DELAY_S = 5

_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)*")


def parse_version(s):
    """"3.2.0" -> (3, 2, 0); anything that isn't plain dotted digits -> None."""
    if not isinstance(s, str) or not _VERSION_RE.fullmatch(s):
        return None
    return tuple(int(part) for part in s.split("."))


def is_newer(remote, local):
    r, l = parse_version(remote), parse_version(local)
    if r is None or l is None:
        return False
    width = max(len(r), len(l))
    r += (0,) * (width - len(r))
    l += (0,) * (width - len(l))
    return r > l


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


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.URLError(f"refusing non-https redirect to {newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, timeout=10):
    """GET `url`, return at most 64KB of body as bytes. https:// only
    (file:// as well when running from source, for local verification)."""
    if not isinstance(url, str):
        raise ValueError("url must be a string")
    low = url.lower()
    if not (low.startswith("https://") or (IS_DEV and low.startswith("file://"))):
        raise ValueError(f"unsupported url scheme: {url!r}")
    request = urllib.request.Request(url, headers={"User-Agent": f"FloweyPet/{version.VERSION}"})
    opener = urllib.request.build_opener(_HttpsOnlyRedirect)
    with opener.open(request, timeout=timeout) as response:
        return response.read(MAX_READ_BYTES)


def should_announce(config, ver):
    return config.get("last_announced_version") != ver


def _positive_number(value, default):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return default
    return value


class UpdateChecker(QObject):
    update_available = pyqtSignal(dict)
    _result = pyqtSignal(object)  # worker thread -> main thread

    def __init__(self, config, fetcher=fetch, use_thread=True):
        super().__init__()
        self.config = config
        self._fetcher = fetcher
        self._use_thread = use_thread
        self._checking = False
        self.available = None

        self._first_timer = QTimer(self)
        self._first_timer.setSingleShot(True)
        self._first_timer.timeout.connect(self.check_now)
        self._periodic_timer = QTimer(self)
        self._periodic_timer.timeout.connect(self.check_now)
        self._result.connect(self._on_result)

    # ------------------------------------------------------------ scheduling
    def _period_ms(self):
        hours = _positive_number(self.config.get("update_check_hours"), 3)
        return int(hours * 3600 * 1000)

    def start(self):
        if not self.config.get("check_updates", True):
            return
        delay = _positive_number(self.config.get("update_first_delay_seconds"), 8)
        self._first_timer.start(int(delay * 1000))
        self._periodic_timer.start(self._period_ms())

    def set_enabled(self, enabled):
        if not enabled:
            self._first_timer.stop()
            self._periodic_timer.stop()
            return
        self._first_timer.start(ENABLE_RECHECK_DELAY_S * 1000)
        self._periodic_timer.start(self._period_ms())

    # -------------------------------------------------------------- checking
    def check_now(self):
        if self._checking:
            return
        url = self.config.get("update_url")
        if not isinstance(url, str) or not url:
            return
        self._checking = True
        if self._use_thread:
            threading.Thread(target=self._work, args=(url,), daemon=True).start()
        else:
            self._work(url)

    def _work(self, url):
        manifest = None
        try:
            manifest = parse_manifest(self._fetcher(url))
            if manifest is None:
                app_log.log_event("检查更新：version.json 格式不对，已忽略")
        except Exception as e:  # noqa: BLE001
            try:
                app_log.log_event(f"检查更新失败：{e!r}")
            except Exception:  # noqa: BLE001
                pass
        self._result.emit(manifest)

    def _on_result(self, manifest):
        self._checking = False
        if manifest is None or not is_newer(manifest["version"], version.VERSION):
            return
        self.available = manifest
        self.update_available.emit(manifest)
