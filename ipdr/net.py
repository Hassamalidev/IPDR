from __future__ import annotations

import socket
import threading
import time

import requests
import urllib3.util.connection as _u3conn
from requests.adapters import HTTPAdapter

QUICK_CONNECT = 3.0
BAD_FOR = 600
_LOCAL = {"localhost", "127.0.0.1", "::1"}

_bad: dict[str, float] = {}
_lock = threading.Lock()
_original = _u3conn.create_connection


def _create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, socket_options=None):
    host, port = address
    if host in _LOCAL:
        return _original(address, timeout, source_address, socket_options)
    try:
        infos = socket.getaddrinfo(host, port, _u3conn.allowed_gai_family(), socket.SOCK_STREAM)
    except OSError:
        return _original(address, timeout, source_address, socket_options)
    now = time.time()
    with _lock:
        infos.sort(key=lambda info: _bad.get(info[4][0], 0) > now - BAD_FOR)
    real = None if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
    error: OSError | None = None
    for n, (family, kind, proto, _, addr) in enumerate(infos):
        last = n == len(infos) - 1
        sock = None
        try:
            sock = socket.socket(family, kind, proto)
            for opt in socket_options or []:
                sock.setsockopt(*opt)
            if source_address:
                sock.bind(source_address)
            limit = real if last else min(QUICK_CONNECT, real or QUICK_CONNECT)
            sock.settimeout(limit)
            sock.connect(addr)
            sock.settimeout(real)
            with _lock:
                _bad.pop(addr[0], None)
            return sock
        except OSError as e:
            error = e
            with _lock:
                _bad[addr[0]] = time.time()
            if sock is not None:
                sock.close()
    raise error or OSError(f"Could not connect to {host}")


_u3conn.create_connection = _create_connection

_session = requests.Session()
_session.mount("https://", HTTPAdapter(pool_connections=8, pool_maxsize=16))
_session.mount("http://", HTTPAdapter(pool_connections=8, pool_maxsize=16))


def request(method: str, url: str, **kw) -> requests.Response:
    try:
        return _session.request(method, url, **kw)
    except requests.exceptions.ConnectionError as e:
        if "RemoteDisconnected" not in str(e) and "Connection aborted" not in str(e) and "reset" not in str(e).lower():
            raise
        return _session.request(method, url, **kw)


def post(url: str, **kw) -> requests.Response:
    return request("POST", url, **kw)


def get(url: str, **kw) -> requests.Response:
    return request("GET", url, **kw)
