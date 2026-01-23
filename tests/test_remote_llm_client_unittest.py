import threading
import time
import json
import unittest
from http.server import HTTPServer, BaseHTTPRequestHandler
from pulse import RemoteLLMClient
import os

class TestHandler(BaseHTTPRequestHandler):
    mode = 'ok'  # 'ok', '500_then_ok', 'stream', 'slow'
    counter = 0

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else b''
        TestHandler.counter += 1

        if self.path.endswith('?stream=1'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
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

class RemoteLLMServerThread:
    def __init__(self):
        self.server = None
        for port in range(8100, 8200):
            try:
                self.server = HTTPServer(('127.0.0.1', port), TestHandler)
                self.port = port
                break
            except OSError:
                continue
        if not self.server:
            raise RuntimeError('No free port')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        time.sleep(0.01)
        return f'http://127.0.0.1:{self.port}'

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=1)

class TestRemoteLLMClient(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = RemoteLLMServerThread()
        cls.base = cls.srv.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def test_direct_json_response(self):
        TestHandler.mode = 'ok'
        c = RemoteLLMClient(self.base + '/ask')
        self.assertTrue(c.available)
        r = c.ask('hello')
        self.assertEqual(r, 'ok')

    def test_retry_on_500_then_success(self):
        TestHandler.mode = '500_then_ok'
        TestHandler.counter = 0
        os.environ['LLM_RETRY_COUNT'] = '2'
        os.environ['LLM_RETRY_BACKOFF'] = '0.01'
        c = RemoteLLMClient(self.base + '/ask')
        r = c.ask('hello')
        self.assertEqual(r, 'ok_after_retry')

    def test_streaming_fallback_on_500(self):
        TestHandler.mode = 'stream'
        TestHandler.counter = 0
        c = RemoteLLMClient(self.base + '/ask')
        r = c.ask('hello')
        self.assertIn('chunk1', r)
        self.assertIn('chunk2', r)

    def test_retry_backoff_sleep(self):
        TestHandler.mode = '500_then_ok'
        TestHandler.counter = 0
        sleeps = []
        real_sleep = time.sleep
        def fake_sleep(s):
            sleeps.append(s)
            # keep it non-blocking
        time.sleep = fake_sleep
        try:
            os.environ['LLM_RETRY_COUNT'] = '2'
            os.environ['LLM_RETRY_BACKOFF'] = '0.1'
            c = RemoteLLMClient(self.base + '/ask')
            r = c.ask('hello')
            self.assertEqual(r, 'ok_after_retry')
            self.assertGreaterEqual(len(sleeps), 1)
            self.assertAlmostEqual(sleeps[0], 0.1, delta=0.01)
        finally:
            time.sleep = real_sleep

if __name__ == '__main__':
    unittest.main()
