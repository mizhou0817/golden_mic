"""Opt-in Windows socket teardown correction, NOT a global asyncio patch.

Uvicorn 0.51: Config(..., loop="backend.windows_asyncio:new_event_loop").
Only the dedicated v2 acceptance host opts in. Daily/production launchers do not.
Windows support is deliberately limited to CPython 3.11.9 and the checked private
implementation below; other layouts fail before a loop/socket is constructed.
Non-Windows callers get their ordinary asyncio policy's new loop unchanged.

Only _make_socket_transport is replaced. Direct TLS construction, pipe transports,
IOCP, subprocesses and SSLProtocol remain stock; start_tls can wrap our socket
transport. This is not a fix for every Windows reset, TLS reset or native crash.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import socket
import sys


_TESTED_LAYOUT = "3ec2af1ab6875a42af03bdbe5cc96d841fb5c7ebfbe74c0ae5d93b693c2d13b0"


def _require_tested_layout() -> None:
    """Fail closed, including patched/private implementations and missing source."""
    if (sys.platform != "win32" or sys.implementation.name != "cpython"
            or tuple(sys.version_info[:3]) != (3, 11, 9)):
        raise RuntimeError("Windows socket correction requires tested CPython 3.11.9")
    from asyncio import base_events as b, proactor_events as p, windows_events as w

    try:
        members = (
            p._ProactorBasePipeTransport.__init__, p._ProactorBasePipeTransport.close,
            p._ProactorBasePipeTransport._force_close,
            p._ProactorBasePipeTransport._call_connection_lost,
            p._ProactorReadPipeTransport._loop_reading,
            p._ProactorBaseWritePipeTransport._loop_writing,
            p._ProactorSocketTransport.__init__, p._ProactorSocketTransport.write_eof,
            p.BaseProactorEventLoop._make_socket_transport,
            p.BaseProactorEventLoop._make_ssl_transport, w.ProactorEventLoop.__init__,
            b.Server._attach, b.Server._detach,
        )
        digest = hashlib.sha256("\n".join(inspect.getsource(f) for f in members).encode("utf-8")).hexdigest()
    except (AttributeError, OSError, TypeError) as error:
        raise RuntimeError("Cannot verify private Windows asyncio layout") from error
    if digest != _TESTED_LAYOUT:
        raise RuntimeError("Unsupported private Windows asyncio layout")


if sys.platform == "win32":
    from asyncio.proactor_events import _ProactorSocketTransport
    from asyncio.windows_events import ProactorEventLoop

    class _SocketTransport(_ProactorSocketTransport):
        def _call_connection_lost(self, exc):
            if self._called_connection_lost:
                return
            # Claim before calling user code: recursive close/abort/lost must not
            # notify twice, including when a protocol cancels/raises/re-enters.
            self._called_connection_lost = True
            self._closing = True
            failures: list[BaseException] = []
            try:
                self._protocol.connection_lost(exc)
            except BaseException as error:
                failures.append(error)
            # Retain _protocol, like the tested stdlib (late IO callbacks use it).
            sock = self._sock
            try:
                if sock is not None and sock.fileno() != -1:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except ConnectionResetError as error:
                        # Only the terminal shutdown operation, NOT notification,
                        # reads/writes/close, errno alone, or arbitrary OSErrors.
                        if getattr(error, "winerror", None) != 10054:
                            raise
            except BaseException as error:
                failures.append(error)
            try:
                if sock is not None:
                    sock.close()
            except BaseException as error:
                failures.append(error)
            finally:
                self._sock = None
                server, self._server = self._server, None
            try:
                if server is not None:
                    server._detach()
            except BaseException as error:
                failures.append(error)
            if len(failures) == 1:
                raise failures[0]
            if failures:
                # Preserve original objects/tracebacks/causes, including a
                # CancelledError. Never replace a protocol failure with cleanup.
                raise BaseExceptionGroup("Socket notification and teardown failed", failures)

    class _WindowsLoop(ProactorEventLoop):
        def _make_socket_transport(self, sock, protocol, waiter=None, extra=None, server=None):
            return _SocketTransport(self, sock, protocol, waiter, extra, server)


def new_event_loop() -> asyncio.AbstractEventLoop:
    """Uvicorn custom loop factory; no policy, handler or installed-module edits."""
    if sys.platform == "win32":
        _require_tested_layout()
        return _WindowsLoop()
    return asyncio.new_event_loop()