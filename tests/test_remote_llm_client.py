import threading
import time
import json
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler
import pytest
from pulse import RemoteLLMClient


class TestHandler(BaseHTTPRequestHandler):
    # server state shared via class attrs
    mode = 'ok'  # 'ok', '500_then_ok', 'stream', 'slow'
    counter = 0

    def do_GET(self):
        # health check
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else b''
        TestHandler.counter += 1

        if self.path.endswith('?stream=1'):
            # streaming SSE endpoint
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            # stream two chunks
            self.wfile.write(b'data: chunk1\n\n')
            self.wfile.flush()
            time.sleep(0.01)
            self.wfile.write(b'data: chunk2\n\n')
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
            return

        if TestHandler.mode == 'ok':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            resp = {'response': 'ok', 'provider': 'test'}
            self.wfile.write(json.dumps(resp).encode('utf-8'))
            return

        if TestHandler.mode == '500_then_ok':
            if TestHandler.counter == 1:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'Internal Error')
                return
            else:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                resp = {'response': 'ok_after_retry', 'provider': 'test'}
                self.wfile.write(json.dumps(resp).encode('utf-8'))
                return

        if TestHandler.mode == 'stream':
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'Internal Error')
            return

        if TestHandler.mode == 'slow':
            time.sleep(0.2)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            resp = {'response': 'slow_ok', 'provider': 'test'}
            self.wfile.write(json.dumps(resp).encode('utf-8'))
            return


@pytest.fixture(scope='module')
def http_server():
    # choose random free port
    server = None
    for port in range(8100, 8200):
        try:
            server = HTTPServer(('127.0.0.1', port), TestHandler)
            break
        except OSError:
            continue
    assert server is not None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    thread.join(timeout=1)


def test_direct_json_response(http_server, monkeypatch):
    TestHandler.mode = 'ok'
    c = RemoteLLMClient(http_server + '/ask')
    assert c.available is True
    r = c.ask('hello')
    assert r == 'ok'


def test_retry_on_500_then_success(http_server, monkeypatch):
    TestHandler.mode = '500_then_ok'
    TestHandler.counter = 0
    monkeypatch.setenv('LLM_RETRY_COUNT', '2')
    c = RemoteLLMClient(http_server + '/ask')
    r = c.ask('hello')
    assert r == 'ok_after_retry'


def test_streaming_fallback_on_500(http_server, monkeypatch):
    TestHandler.mode = 'stream'
    TestHandler.counter = 0
    # ensure streaming path will be tried
    c = RemoteLLMClient(http_server + '/ask')
    r = c.ask('hello')
    assert 'chunk1' in r and 'chunk2' in r


def test_retry_backoff_sleep(monkeypatch, http_server):
    # make server return 500 for a while, ensure sleep is called with expected backoff
    TestHandler.mode = '500_then_ok'
    TestHandler.counter = 0
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr('time.sleep', fake_sleep)
    monkeypatch.setenv('LLM_RETRY_COUNT', '2')
    monkeypatch.setenv('LLM_RETRY_BACKOFF', '0.1')
    c = RemoteLLMClient(http_server + '/ask')
    r = c.ask('hello')
    assert r == 'ok_after_retry'
    assert len(sleeps) >= 1
    # first sleep should be around 0.1
    assert pytest.approx(sleeps[0], rel=0.1) == 0.1
