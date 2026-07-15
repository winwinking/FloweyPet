# -*- coding: utf-8 -*-
"""Single-instance guard: refuses to let a second FloweyPet process open a
second pet window. Built on QLocalServer/QLocalSocket (a named-pipe-backed
handshake) rather than a lock *file*, specifically so a killed/crashed
process can never leave a stale lock that blocks every future launch --
see try_claim()'s ordering (connect-probe first, removeServer only after
the probe fails) for why that's safe.

Usage (see main.py): construct right after QApplication exists, call
try_claim() before creating any window. If it returns False, a live
instance already answered and has been sent the activate message -- exit
immediately without creating a window. If True, this process owns the
lock; set .on_activate_requested to a callable before entering the event
loop, and call .close() on shutdown (e.g. via app.aboutToQuit).
"""
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

import app_log

ACTIVATE_MESSAGE = b"ACTIVATE"


class SingleInstanceGuard:
    def __init__(self, name, connect_timeout_ms):
        self.name = name
        self.connect_timeout_ms = connect_timeout_ms
        self.on_activate_requested = None  # callable(), set by the caller once its window exists
        self._server = None
        self._incoming = []  # keep references so in-flight QLocalSockets aren't GC'd

    def try_claim(self):
        """Returns True if this process owns the lock (should proceed to
        create its window), False if a live instance already answered (this
        process must exit immediately without creating anything)."""
        probe = QLocalSocket()
        probe.connectToServer(self.name)
        if probe.waitForConnected(self.connect_timeout_ms):
            probe.write(ACTIVATE_MESSAGE)
            probe.waitForBytesWritten(self.connect_timeout_ms)
            probe.disconnectFromServer()
            app_log.log_event("检测到重复启动，已通知主实例并退出")
            return False

        # Nothing answered within the timeout -- either there's truly no
        # other instance, or a previous instance crashed/was killed and left
        # a stale lock behind. Either way it's now safe to clear it: this
        # removeServer() call only ever runs *after* a connect attempt has
        # already failed, never before, so it can't rip the lock out from
        # under a genuinely live instance.
        app_log.log_event("未探测到活动实例，清理可能的残留锁")
        QLocalServer.removeServer(self.name)

        self._server = QLocalServer()
        self._server.newConnection.connect(self._on_new_connection)
        if not self._server.listen(self.name):
            app_log.log_event(f"单实例服务监听失败（{self._server.errorString()}），本次启动跳过单实例保护")
            self._server = None
        return True

    def _on_new_connection(self):
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            self._incoming.append(conn)
            conn.readyRead.connect(lambda c=conn: self._on_ready_read(c))
            conn.disconnected.connect(lambda c=conn: self._forget(c))

    def _on_ready_read(self, conn):
        data = bytes(conn.readAll())
        if ACTIVATE_MESSAGE in data:
            app_log.log_event("收到重复启动的 ACTIVATE 请求")
            if self.on_activate_requested:
                self.on_activate_requested()

    def _forget(self, conn):
        if conn in self._incoming:
            self._incoming.remove(conn)
        conn.deleteLater()

    def close(self):
        if self._server is not None:
            self._server.close()
            QLocalServer.removeServer(self.name)
            self._server = None
