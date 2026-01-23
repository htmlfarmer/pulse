import threading
import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import unittest
from pulse import RemoteLLMClient

class ProviderFailHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else b''
        try:
            j = json.loads(body.decode('utf-8') or '{}')
        except Exception:
            j = {}
        provider = j.get('provider')
        # Record the first provider we saw so tests can assert defaults were sent
        if not hasattr(self.server, 'first_provider'):
            self.server.first_provider = provider
        # Simulate gemini error unless provider is 'local'
        if provider == 'local':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'response': 'local_ok'}).encode('utf-8'))
        else:
            # Return textual gemini API key leak message
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'gemini-3-flash-preview\n403 Your API key was reported as leaked. Please use another API key.')

class TestProviderFailover(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(('127.0.0.1', 0), ProviderFailHandler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.01)
        cls.base = f'http://127.0.0.1:{cls.port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=1)

    def test_failover_to_local_provider(self):
        c = RemoteLLMClient(self.base + '/ask')
        self.assertTrue(c.available)
        r = c.ask('hello world')
        self.assertEqual(r, 'local_ok')
        # Ensure the initial provider used was the Gemini default
        self.assertEqual(self.server.first_provider, 'gemini-2.5-flash-lite')

if __name__ == '__main__':
    unittest.main()
