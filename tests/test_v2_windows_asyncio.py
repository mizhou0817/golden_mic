"""Focused socketpair-only regression; no application/server is imported or run.

Ordinary discovery exercises units and native socketpairs under the existing v2
guard's socketpair exemption. Synthetic Python PIPE children require the explicit
standalone --native runner below (the v2 guard correctly forbids Python children).
The standalone runner uses the unchanged acceptance NetworkGuard plus a stricter
audit boundary. It emits safe counts/types/source coordinates only in fresh TEMP.
"""
from __future__ import annotations

import ast
import asyncio
import builtins
import gc
import hashlib
import inspect
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import sys
import tempfile
import threading
import unittest
import warnings
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, cast
from unittest.mock import patch

from backend import windows_asyncio as correction

ROOT = Path(__file__).resolve().parents[1]
NATIVE = False  # Only this file's explicit --native runner enables child processes.
EVIDENCE: dict[str, Any] = {"reproductions": [], "loopExceptions": [], "bytes": [], "pipes": [], "http": []}
CHILD = "import sys; sys.stdout.buffer.write(b'o'*65536); sys.stdout.buffer.flush(); sys.stderr.buffer.write(b'e'*65536); sys.stderr.buffer.flush(); sys.stdin.buffer.read(); sys.stdout.buffer.write(b'end')"


def fact(error):
    return {"type": type(error).__name__, "winerror": getattr(error, "winerror", None)}


async def turns(count=4):
    for _ in range(count):
        future = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(future.set_result, None)
        await future


@contextmanager
def owned_loop(stock=False):
    loop = asyncio.ProactorEventLoop() if stock else correction.new_event_loop()
    contexts = []
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))
    try:
        yield loop, contexts
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()
        EVIDENCE["loopExceptions"].extend({"stock": stock, **fact(c.get("exception"))} for c in contexts)
        if pending:
            raise AssertionError("Unexpected pending tasks at loop teardown")


class Protocol(asyncio.Protocol):
    def __init__(self):
        self.lost = asyncio.get_running_loop().create_future()
        self.received = bytearray()
        self.calls = []

    def connection_made(self, transport):
        self.transport = transport

    def data_received(self, data):
        self.received.extend(data)

    def connection_lost(self, exc):
        self.calls.append(exc)
        if not self.lost.done():
            self.lost.set_result(None)

    def eof_received(self):
        return False


class ImportTests(unittest.TestCase):
    def test_non_windows_import_without_windows_symbols(self):
        # Execute the actual source with Windows-only imports explicitly denied.
        # This is branch/import isolation on Windows, NOT a Linux runtime claim.
        source = inspect.getsource(correction)
        imported = []
        original = builtins.__import__
        fake_sys = SimpleNamespace(platform="linux")
        sentinel = object()
        fake_asyncio = SimpleNamespace(new_event_loop=lambda: sentinel)

        def importing(name, *args, **kwargs):
            imported.append(name)
            if name == "sys":
                return fake_sys
            if name == "asyncio":
                return fake_asyncio
            if name.startswith("asyncio."):
                raise AssertionError("Windows import on non-Windows")
            return original(name, *args, **kwargs)

        namespace: dict[str, Any] = {"__builtins__": dict(vars(builtins), __import__=importing)}
        exec(compile(source, str(ROOT / "backend/windows_asyncio.py"), "exec"), namespace)
        self.assertIs(namespace["new_event_loop"](), sentinel)
        self.assertNotIn("asyncio.windows_events", imported)

    def test_uvicorn_custom_factory_without_loading_app(self):
        import uvicorn
        self.assertEqual(uvicorn.__version__, "0.51.0")
        async def app(scope, receive, send):
            raise AssertionError("Must not run an application")
        # Import-string resolution must not construct a loop or mutate policy.
        policy = asyncio.get_event_loop_policy()
        with patch.object(correction, "new_event_loop", wraps=correction.new_event_loop) as factory:
            config = uvicorn.Config(app, loop="backend.windows_asyncio:new_event_loop", log_config=None)
            self.assertIs(config.get_loop_factory(), factory)
            factory.assert_not_called()
            self.assertFalse(config.loaded)
            daily = uvicorn.Config(app, loop="none", log_config=None)
            self.assertIsNone(daily.get_loop_factory())
            factory.assert_not_called()
            self.assertIs(asyncio.get_event_loop_policy(), policy)
            if sys.platform == "win32":
                default = uvicorn.Config(app, log_config=None)
                self.assertIs(default.get_loop_factory(), asyncio.ProactorEventLoop)
                # A supported Windows runtime must execute, not silently skip.
                selected = config.get_loop_factory()
                assert selected is not None
                loop = selected()
                try:
                    factory.assert_called_once_with()
                    self.assertIsInstance(loop, correction._WindowsLoop)
                finally:
                    loop.close()


@unittest.skipUnless(sys.platform == "win32", "Windows transport implementation")
class WindowsTests(unittest.TestCase):
    def test_layout_fail_closed_before_loop_construction(self):
        correction._require_tested_layout()
        for replacement in ((3, 11, 8), (3, 12, 0), (3, 14, 0)):
            with patch.object(correction.sys, "version_info", replacement), patch.object(correction, "_WindowsLoop") as constructor:
                with self.assertRaises(RuntimeError):
                    correction.new_event_loop()
                constructor.assert_not_called()
        with patch.object(correction, "_TESTED_LAYOUT", "unsupported"), patch.object(correction, "_WindowsLoop") as constructor:
            with self.assertRaises(RuntimeError):
                correction.new_event_loop()
            constructor.assert_not_called()
        with patch.object(correction.inspect, "getsource", side_effect=OSError()):
            with self.assertRaises(RuntimeError):
                correction.new_event_loop()

    def test_stock_classes_and_policy_unchanged(self):
        from asyncio import proactor_events as p
        policy = asyncio.get_event_loop_policy()
        callback = p._ProactorSocketTransport._call_connection_lost
        with owned_loop() as (loop, errors):
            self.assertIsInstance(loop, asyncio.ProactorEventLoop)
            self.assertIs(type(loop)._make_ssl_transport, p.BaseProactorEventLoop._make_ssl_transport)
            self.assertIs(type(loop)._make_subprocess_transport, asyncio.ProactorEventLoop._make_subprocess_transport)
            self.assertEqual(errors, [])
        self.assertIs(asyncio.get_event_loop_policy(), policy)
        self.assertIs(p._ProactorSocketTransport._call_connection_lost, callback)

    def test_notification_cleanup_failure_matrix(self):
        """Injected UNIT errors only; the native FIN/write/RST repro is separate."""
        class FakeSocket:
            def __init__(self, shutdown_error=None, close_error=None, closed=False):
                self.shutdown_error, self.close_error = shutdown_error, close_error
                self.closed, self.shutdowns, self.closes = closed, 0, 0
            def fileno(self):
                return -1 if self.closed else 123
            def shutdown(self, how):
                self.shutdowns += 1
                if self.shutdown_error:
                    raise self.shutdown_error
            def close(self):
                self.closes += 1
                self.closed = True
                if self.close_error:
                    raise self.close_error

        def reset(code):
            error = ConnectionResetError("unit fixture only")
            error.winerror = code
            return error

        for mode in ("success", "10054", "other_reset", "oserror", "protocol",
                     "protocol10054", "cancel", "all_fail", "cancel_all_fail", "close10054", "closed", "none", "no_server"):
            with self.subTest(mode=mode):
                shutdown = reset(10054) if mode == "10054" else reset(10053) if mode == "other_reset" else OSError(5, "unit") if mode in ("oserror", "all_fail", "cancel_all_fail") else None
                notify = ValueError("unit") if mode in ("protocol", "all_fail") else reset(10054) if mode == "protocol10054" else asyncio.CancelledError() if mode in ("cancel", "cancel_all_fail") else None
                close_error = RuntimeError("close unit") if mode in ("all_fail", "cancel_all_fail") else reset(10054) if mode == "close10054" else None
                detach_error = LookupError("detach unit") if mode in ("all_fail", "cancel_all_fail") else None
                sock = None if mode == "none" else FakeSocket(shutdown, close_error, mode == "closed")
                incoming = RuntimeError("original incoming cause")
                if notify is not None:
                    notify.__cause__ = incoming
                calls, detached = [], []
                transport = object.__new__(correction._SocketTransport)
                def connection_lost(exc):
                    calls.append(exc)
                    transport.close()
                    transport._call_connection_lost(exc)  # re-entrant duplicate
                    if notify is not None:
                        raise notify
                def detach():
                    detached.append(True)
                    if detach_error:
                        raise detach_error
                protocol = SimpleNamespace(connection_lost=connection_lost)
                transport._protocol = protocol
                transport._sock = sock
                transport._server = None if mode == "no_server" else SimpleNamespace(_detach=detach)
                transport._called_connection_lost = False
                transport._closing = False
                expected = [e for e in (notify, shutdown if mode != "10054" else None, close_error, detach_error) if e is not None]
                if expected:
                    with self.assertRaises(BaseException) as caught:
                        transport._call_connection_lost(incoming)
                    actual = list(caught.exception.exceptions) if isinstance(caught.exception, BaseExceptionGroup) else [caught.exception]
                    self.assertEqual(actual, expected)
                    if mode == "cancel_all_fail":
                        self.assertIs(type(caught.exception), BaseExceptionGroup)
                    if notify is not None:
                        self.assertIs(notify.__cause__, incoming)
                else:
                    transport._call_connection_lost(incoming)
                transport.close()
                transport._call_connection_lost(None)
                self.assertEqual(calls, [incoming])
                self.assertEqual(len(detached), 0 if mode == "no_server" else 1)
                self.assertTrue(transport._called_connection_lost)
                self.assertTrue(transport.is_closing())
                self.assertIsNone(transport._sock)
                self.assertIsNone(transport._server)
                self.assertIs(transport.get_protocol(), protocol)
                if sock:
                    self.assertEqual(sock.closes, 1)
                    self.assertEqual(sock.shutdowns, 0 if mode == "closed" else 1)

    def test_real_stock_and_corrected_30_cases_each(self):
        from uvicorn import Config
        from uvicorn.server import ServerState
        from uvicorn.protocols.http.auto import AutoHTTPProtocol
        async def app(scope, receive, send):
            raise AssertionError("No HTTP request permitted")
        config = Config(app, log_config=None, access_log=False, proxy_headers=False, lifespan="off", ws="none")
        config.load()
        for stock in (True, False):
            with owned_loop(stock) as (loop, contexts):
                async def cases():
                    for base in (Protocol, AutoHTTPProtocol):
                        for action in ("fin", "rst", "fin_write_rst", "fin_write_rst_abort", "protocol_error"):
                            for repeat in range(3):
                                start = len(contexts)
                                a, b = socket.socketpair()
                                a.setblocking(False)
                                b.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("HH", 1, 0))
                                calls, timeline = [], []
                                lost = loop.create_future()
                                state = ServerState()
                                class Observed(base):
                                    def eof_received(self):
                                        timeline.append("fin")
                                        if action.startswith("fin_write_rst"):
                                            self.transport.write(b"x" * 4096)
                                            timeline.append("write4096")
                                            b.close()
                                            timeline.append("native_rst")
                                            if action.endswith("abort"):
                                                self.transport.abort()
                                        return super().eof_received()
                                    def connection_lost(self, exc):
                                        calls.append(exc)
                                        super().connection_lost(exc)
                                        if not lost.done():
                                            lost.set_result(None)
                                        if action == "protocol_error":
                                            raise ValueError("Intentional protocol fixture")
                                protocol = Observed() if base is Protocol else Observed(config, state, {}, _loop=loop)
                                server = asyncio.base_events.Server(loop, [], lambda: protocol, None, 1, None)
                                made = loop.create_future()
                                transport = loop._make_socket_transport(a, protocol, made, server=server)
                                try:
                                    await made
                                    self.assertEqual(server._active_count, 1)
                                    if action.startswith("fin_write_rst"):
                                        b.shutdown(socket.SHUT_WR)
                                    else:
                                        if action != "rst":
                                            b.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("HH", 0, 0))
                                        b.close()
                                    await asyncio.wait_for(asyncio.shield(lost), 3)
                                    await turns()
                                    errors = [fact(c["exception"]) for c in contexts[start:]]
                                    leaked = transport._sock is not None
                                    EVIDENCE["reproductions"].append({"stock": stock, "protocol": base.__name__, "action": action, "repeat": repeat, "timeline": timeline, "exceptions": errors, "notified": len(calls), "socketRetained": leaked, "serverActive": server._active_count})
                                    self.assertEqual(len(calls), 1)
                                    self.assertEqual(len(state.connections), 0)
                                    self.assertEqual(len(state.tasks), 0)
                                    if stock and action.startswith("fin_write_rst"):
                                        self.assertEqual(errors, [{"type": "ConnectionResetError", "winerror": 10054}])
                                        self.assertTrue(leaked)
                                        self.assertFalse(transport._called_connection_lost)
                                        self.assertEqual(server._active_count, 1)
                                    else:
                                        self.assertEqual(errors, [{"type": "ValueError", "winerror": None}] if action == "protocol_error" else [])
                                        self.assertFalse(leaked)
                                        self.assertEqual(server._active_count, 0)
                                    transport.close()
                                    if not stock:
                                        transport.abort()
                                    await turns(2)
                                    self.assertEqual(len(calls), 1)
                                    if not stock:
                                        self.assertIsNone(transport._sock)
                                        self.assertIsNone(transport._server)
                                        self.assertIsNone(transport._read_fut)
                                        self.assertIsNone(transport._write_fut)
                                        self.assertEqual(transport.get_write_buffer_size(), 0)
                                finally:
                                    # Stock defects recorded BEFORE owned cleanup.
                                    a.close()
                                    b.close()
                                    if stock and transport._server is not None:
                                        transport._server._detach()
                                        transport._server = None
                                        transport._sock = None
                                        transport._called_connection_lost = True
                                    server.close()
                                    transport.close()
                                    await turns(2)
                loop.run_until_complete(asyncio.wait_for(cases(), 30))

    def test_socketpair_bytes_and_pending_write_cancellation(self):
        with owned_loop() as (loop, errors):
            async def cases():
                payload = bytes(range(256)) * 1024
                a, b = socket.socketpair()
                a.setblocking(False)
                b.setblocking(False)
                p, q = Protocol(), Protocol()
                ta, _ = await loop.connect_accepted_socket(lambda: p, a)
                tb, _ = await loop.connect_accepted_socket(lambda: q, b)
                try:
                    ta.write(payload)
                    tb.write(payload[::-1])
                    ta.write_eof()
                    tb.write_eof()
                    await asyncio.wait_for(asyncio.gather(p.lost, q.lost), 5)
                    self.assertEqual(bytes(q.received), payload)
                    self.assertEqual(bytes(p.received), payload[::-1])
                    EVIDENCE["bytes"].append({"kind": "plain", "eachDirection": len(payload), "exact": True})
                finally:
                    ta.abort(); tb.abort()
                    await turns()
                    a.close(); b.close()
                for cancel_in_lost in (False, True):
                    a, b = socket.socketpair()
                    a.setblocking(False)
                    a.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
                    p = Protocol()
                    ta, _ = await loop.connect_accepted_socket(lambda: p, a)
                    gate = loop.create_future()
                    started = loop.create_future()
                    async def writer():
                        try:
                            ta.write(b"w" * (2 * 1024 * 1024))
                            ta.write(b"z" * (2 * 1024 * 1024))
                            # Assert while still in the writing turn, not after
                            # IOCP may have completed the native send.
                            self.assertGreater(ta.get_write_buffer_size(), 0)
                            started.set_result(None)
                            await gate
                        finally:
                            ta.abort()
                    task = loop.create_task(writer())
                    try:
                        await asyncio.wait_for(started, 3)
                        if cancel_in_lost:
                            original = p.connection_lost
                            def lost(exc):
                                original(exc)
                                task.cancel()
                                ta.abort()
                            p.connection_lost = lost
                            ta.abort()
                        else:
                            task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await task
                        await asyncio.wait_for(p.lost, 3)
                        await turns()
                        self.assertEqual(len(p.calls), 1)
                        self.assertIsNone(ta._sock)
                        self.assertIsNone(ta._write_fut)
                        self.assertEqual(ta.get_write_buffer_size(), 0)
                    finally:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        ta.abort(); b.close()
                        await turns()
                        a.close()
            loop.run_until_complete(asyncio.wait_for(cases(), 20))
            self.assertEqual(errors, [])
        # An independent stock/custom ablation with actual httptools HTTP bytes,
        # not merely an HTTP protocol class receiving EOF without a request.
        for stock in (True, False):
            with owned_loop(stock) as (loop, errors):
                loop.run_until_complete(asyncio.wait_for(self._http_payload_and_cancel(loop, stock), 15))
                self.assertEqual(errors, [])

    async def _http_payload_and_cancel(self, loop, stock):
        from uvicorn import Config
        from uvicorn.server import ServerState
        from uvicorn.protocols.http.auto import AutoHTTPProtocol
        payload = bytes(range(256)) * 256
        for cancel in (False, True):
            received = bytearray()
            blocked = loop.create_future()
            cancelled = []
            async def app(scope: dict[str, Any],
                          receive: Callable[[], Awaitable[dict[str, Any]]],
                          send: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
                self.assertEqual(scope["method"], "POST")
                while True:
                    message = await receive()
                    self.assertEqual(message["type"], "http.request")
                    received.extend(message["body"])
                    if not message.get("more_body", False):
                        break
                self.assertEqual(bytes(received), payload)
                body = b"w" * (2 * 1024 * 1024) if cancel else payload[::-1]
                await send({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-length", str(len(body) + (4 if cancel else 0)).encode("ascii"))]})
                try:
                    await send({"type": "http.response.body", "body": body, "more_body": cancel})
                    if cancel:
                        # First send queues native IO; second send must await
                        # real uvicorn flow control while the peer is not reading.
                        self.assertTrue(protocol.flow.write_paused)
                        self.assertGreater(transport.get_write_buffer_size(), 0)
                        blocked.set_result(None)
                        await send({"type": "http.response.body", "body": b"tail"})
                except asyncio.CancelledError:
                    cancelled.append(True)
                    raise
            config = Config(app, log_config=None, access_log=False, proxy_headers=False,
                            lifespan="off", ws="none")
            config.load()
            state = ServerState()
            # Uvicorn annotates AutoHTTPProtocol as type[asyncio.Protocol],
            # but its installed runtime is HttpToolsProtocol(config, state, ...).
            protocol = cast(Any, AutoHTTPProtocol)(config, state, {}, _loop=loop)
            a, b = socket.socketpair()
            a.setblocking(False); b.setblocking(False)
            a.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
            server = asyncio.base_events.Server(loop, [], lambda: protocol, None, 1, None)
            made = loop.create_future()
            transport = loop._make_socket_transport(a, protocol, made, server=server)
            try:
                await made
                self.assertEqual(server._active_count, 1)
                self.assertIsNotNone(transport._read_fut)
                request = b"POST /synthetic HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nContent-Length: 65536\r\n\r\n" + payload
                await loop.sock_sendall(b, request)
                if cancel:
                    await asyncio.wait_for(blocked, 5)
                    tasks = list(state.tasks)
                    self.assertEqual(len(tasks), 1)
                    self.assertFalse(tasks[0].done())
                    # Uvicorn intentionally logs and catches app cancellation;
                    # capture/assert it, never claim an absence of this error.
                    with self.assertLogs("uvicorn.error", level="ERROR") as logs:
                        tasks[0].cancel()
                        await asyncio.gather(*tasks)
                    self.assertEqual(len(logs.records), 1)
                    exception_info = logs.records[0].exc_info
                    assert exception_info is not None
                    self.assertIsInstance(exception_info[1], asyncio.CancelledError)
                    self.assertEqual(cancelled, [True])
                    transport.abort()  # discard remaining queued response bytes
                else:
                    response = bytearray()
                    while chunk := await loop.sock_recv(b, 65536):
                        response.extend(chunk)
                    header, body = bytes(response).split(b"\r\n\r\n", 1)
                    self.assertTrue(header.startswith(b"HTTP/1.1 200 OK\r\n"))
                    self.assertIn(b"content-length: 65536", header.lower())
                    self.assertEqual(body, payload[::-1])
                await turns(8)
                self.assertEqual(bytes(received), payload)
                self.assertEqual(len(state.connections), 0)
                self.assertEqual(len(state.tasks), 0)
                self.assertEqual(server._active_count, 0)
                self.assertTrue(transport._called_connection_lost)
                self.assertIs(transport.get_protocol(), protocol)
                for field in ("_sock", "_server", "_read_fut", "_write_fut", "_buffer"):
                    self.assertIsNone(getattr(transport, field))
                self.assertEqual(transport.get_write_buffer_size(), 0)
                self.assertFalse(protocol.flow.write_paused)
                EVIDENCE["http"].append({"stock": stock, "cancel": cancel,
                    "requestBytes": len(received), "exactResponseBytes": 0 if cancel else len(payload),
                    "expectedAppCancellationLogs": 1 if cancel else 0, "serverActive": server._active_count})
            finally:
                tasks = list(state.tasks)
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                transport.abort(); b.close()
                await turns()
                a.close(); server.close()

    def test_tls_direct_and_start_tls_bytes(self):
        # Existing CPython synthetic test certificate, never user credentials.
        certificate = Path(sys.base_prefix) / "Lib/test/certdata/keycert.pem"
        self.assertTrue(certificate.is_file(), "Local synthetic CPython certificate required")
        with tempfile.TemporaryDirectory(prefix="gm-windows-tls-") as temporary:
            pem = Path(temporary) / "synthetic.pem"
            pem.write_bytes(certificate.read_bytes())
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(str(pem))
            client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client_context.check_hostname = False
            client_context.verify_mode = ssl.CERT_NONE  # expired synthetic cert, local owned pair only
            with owned_loop() as (loop, errors):
                async def cases():
                    for upgrade in (False, True):
                        a, b = socket.socketpair()
                        a.setblocking(False); b.setblocking(False)
                        p, q = Protocol(), Protocol()
                        ta = tb = None
                        try:
                            if upgrade:
                                ta, _ = await loop.connect_accepted_socket(lambda: p, a)
                                tb, _ = await loop.connect_accepted_socket(lambda: q, b)
                                self.assertIsInstance(ta, correction._SocketTransport)
                                ta, tb = await asyncio.wait_for(asyncio.gather(
                                    loop.start_tls(ta, p, server_context, server_side=True),
                                    loop.start_tls(tb, q, client_context, server_hostname="localhost")), 5)
                                assert ta is not None and tb is not None
                                p.transport, q.transport = ta, tb
                            else:
                                left, right = await asyncio.wait_for(asyncio.gather(
                                    loop.connect_accepted_socket(lambda: p, a, ssl=server_context),
                                    loop.create_connection(lambda: q, sock=b, ssl=client_context, server_hostname="localhost")), 5)
                                ta, tb = left[0], right[0]
                            payload = bytes(range(256)) * 256
                            ta.write(payload); tb.write(payload[::-1])
                            async def received():
                                while len(p.received) < len(payload) or len(q.received) < len(payload):
                                    await turns()
                            await asyncio.wait_for(received(), 5)
                            self.assertEqual(bytes(p.received), payload[::-1])
                            self.assertEqual(bytes(q.received), payload)
                            ta.close(); tb.close()
                            await asyncio.wait_for(asyncio.gather(p.lost, q.lost), 5)
                            self.assertEqual(len(p.calls), 1); self.assertEqual(len(q.calls), 1)
                            EVIDENCE["bytes"].append({"kind": "start_tls" if upgrade else "direct_tls_stock", "eachDirection": len(payload), "exact": True})
                        finally:
                            if ta: ta.abort()
                            if tb: tb.abort()
                            await turns()
                            a.close(); b.close()
                loop.run_until_complete(asyncio.wait_for(cases(), 25))
                self.assertEqual(errors, [])

    def test_native_subprocess_pipes_eof_and_cancel(self):
        if not NATIVE:
            self.skipTest("Explicit standalone --native only; never bypass v2 subprocess guard")
        with owned_loop() as (loop, errors):
            async def cases():
                for cancel in (False, True):
                    process = await asyncio.create_subprocess_exec(sys.executable, "-I", "-B", "-c", CHILD,
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                        env={k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR"}})
                    try:
                        assert process.stdout is not None and process.stderr is not None
                        if cancel:
                            self.assertEqual(await asyncio.wait_for(process.stdout.readexactly(65536), 5), b"o" * 65536)
                            self.assertEqual(await asyncio.wait_for(process.stderr.readexactly(65536), 5), b"e" * 65536)
                            waiting = loop.create_task(process.wait())
                            await turns()
                            waiting.cancel()
                            with self.assertRaises(asyncio.CancelledError):
                                await waiting
                            process.kill()
                        out, err = await asyncio.wait_for(process.communicate(input=b""), 5)
                        if not cancel:
                            self.assertEqual(out, b"o" * 65536 + b"end")
                            self.assertEqual(err, b"e" * 65536)
                            self.assertEqual(process.returncode, 0)
                        self.assertIsNotNone(process.returncode)
                        EVIDENCE["pipes"].append({"cancel": cancel, "exitCode": process.returncode, "stdoutEOF": process.stdout.at_eof(), "stderrEOF": process.stderr.at_eof()})
                    finally:
                        if process.returncode is None:
                            process.kill()
                            await asyncio.wait_for(process.communicate(), 5)
                        await process.wait()
                        await turns()
            loop.run_until_complete(asyncio.wait_for(cases(), 20))
            self.assertEqual(errors, [])


def load_tests(loader, standard_tests, pattern):
    """Normal v2 discovery has eight cases, not a ninth permanently skipped child."""
    suite = loader.suiteClass()
    suite.addTests(loader.loadTestsFromTestCase(ImportTests))
    suite.addTests(WindowsTests(name) for name in loader.getTestCaseNames(WindowsTests)
                   if NATIVE or name != "test_native_subprocess_pipes_eof_and_cancel")
    return suite


def reset_counts():
    """Intentional stock reproduction errors are not corrected shutdown errors."""
    return {"stockExpected10054": sum(e["stock"] and e["winerror"] == 10054 for e in EVIDENCE["loopExceptions"]),
            "corrected10054": sum(not e["stock"] and e["winerror"] == 10054 for e in EVIDENCE["loopExceptions"]),
            "stockExpectedProtocolErrors": sum(e["stock"] and e["type"] == "ValueError" for e in EVIDENCE["loopExceptions"]),
            "correctedExpectedProtocolErrors": sum(not e["stock"] and e["type"] == "ValueError" for e in EVIDENCE["loopExceptions"])}


def native_main():
    """One-shot bounded standalone runner, no app import/build/listener/env file."""
    global NATIVE
    if sys.argv[1:] != ["--native"] or sys.platform != "win32":
        raise SystemExit("Explicit Windows --native required")
    NATIVE = True
    sys.dont_write_bytecode = True
    os.environ.clear()
    out = Path(tempfile.mkdtemp(prefix="gm-windows-correction-"))
    watched = ("backend/windows_asyncio.py", "tests/test_v2_windows_asyncio.py", "tests/v2_acceptance_server.py", "backend/main.py", "tests/run_v2_validation.py", "tests/workspace_fixture.py")
    def hashes():
        return {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in watched}
    before = hashes()
    sockets, denials = [], []
    local = threading.local()
    pair = socket.socketpair
    def owned_pair(*args, **kwargs):
        previous = getattr(local, "pair", False)
        local.pair = True
        try:
            a, b = pair(*args, **kwargs)
            sockets.extend((a, b))
            assert all(s.getsockname()[0] == "127.0.0.1" and s.getsockname()[1] not in {8000, 8787} for s in (a, b))
            return a, b
        finally:
            local.pair = previous
    def deny(event):
        denials.append(event)
        raise RuntimeError("Focused transport test boundary")
    import subprocess
    approved = subprocess.list2cmdline([sys.executable, "-I", "-B", "-c", CHILD])
    def audit(event, args):
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo"}:
            address = args[1] if event != "socket.getaddrinfo" else args[:2]
            if not (getattr(local, "pair", False) and isinstance(address, tuple) and address[0] == "127.0.0.1" and address[1] not in {8000, 8787}):
                deny(event)
        elif event == "subprocess.Popen":
            if args[1] != approved or args[0] not in (None, sys.executable):
                deny(event)
        elif event in {"sqlite3.connect", "os.system", "os.startfile", "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo", "socket.sendto", "socket.sendmsg"}:
            deny(event)
        elif event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            p = Path(os.fsdecode(args[0])).resolve()
            writing = bool(args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if p.name.startswith(".env") or any(p.is_relative_to(ROOT / n) for n in ("data", "eval", "eval_sample", "models", "canary_test/artifacts", "frontend/dist")) or (writing and not p.is_relative_to(out)):
                deny(event)
    with ExitStack() as stack, warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        stack.enter_context(patch.object(tempfile, "tempdir", str(out)))
        stack.enter_context(patch.object(socket, "socketpair", owned_pair))
        stack.enter_context(patch("platform._syscmd_ver", return_value=("", "", "")))
        sys.addaudithook(audit)
        # Exact existing guard class, avoiding unrelated fixture/provider imports.
        tree = ast.parse((ROOT / "tests/workspace_fixture.py").read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NetworkGuard")
        namespace = dict(globals(), Any=object, PROJECT_ROOT=ROOT, SafetyError=RuntimeError)
        exec(compile(ast.Module(body=[node], type_ignores=[]), "workspace_fixture.NetworkGuard", "exec"), namespace)
        guard = namespace["NetworkGuard"](1, out, out / "unused.json")
        guard.install(stack)
        from tests.run_v2_validation import SafeResult
        result = SafeResult(before)
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        if suite.countTestCases() != 9:
            raise RuntimeError("Focused runner must discover all nine tests")
        suite.run(result)
        gc.collect()
        after = hashes()
        summary = {"tests": result.testsRun, "passed": result.passed, "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped), "events": result.events, "evidence": EVIDENCE, "sourceBefore": before, "sourceAfter": after, "sourceDrift": [n for n in before if before[n] != after[n]], "guardDenied": guard.denied, "safetyDenials": denials, "allOwnedSocketsClosed": all(s.fileno() == -1 for s in sockets), "ownedSocketCount": len(sockets), "resourceWarnings": [type(w.message).__name__ for w in caught if issubclass(w.category, ResourceWarning)], "pid": os.getpid()}
        summary["windowsResetCounts"] = reset_counts()
        (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps({"evidenceDirectory": str(out), **{k: v for k, v in summary.items() if k not in {"evidence", "sourceBefore", "sourceAfter"}}}, ensure_ascii=True))
    return int(result.testsRun != 9 or bool(result.skipped) or not result.wasSuccessful() or bool(summary["sourceDrift"] or denials or guard.denied or summary["resourceWarnings"]) or not summary["allOwnedSocketsClosed"])


if __name__ == "__main__":
    raise SystemExit(native_main())