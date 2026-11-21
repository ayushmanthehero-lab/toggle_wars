"""Controllable dependency used only for CI Guard's labeled demo."""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class InventoryServer(ThreadingHTTPServer):
    healthy = True
    lock = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path not in ('/health', '/inventory'):
            return self.respond(404, {'detail': 'Not found'})
        if self.path == '/inventory':
            time.sleep(0.25)
        with self.server.lock:
            healthy = self.server.healthy
        if not healthy:
            return self.respond(503, {'detail': 'INVENTORY_DEPENDENCY_UNAVAILABLE'})
        self.respond(200, {'status': 'healthy', 'stock': 42})

    def do_POST(self):
        if self.path != '/__control':
            return self.respond(404, {'detail': 'Not found'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > 1024:
                raise ValueError('too large')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body.get('healthy'), bool):
                raise ValueError('healthy must be boolean')
        except (ValueError, TypeError):
            return self.respond(400, {'detail': 'Expected boolean healthy'})
        with self.server.lock:
            self.server.healthy = body['healthy']
        self.respond(200, {'healthy': body['healthy'], 'demo': True})

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8090)
    args = parser.parse_args()
    InventoryServer((args.host, args.port), Handler).serve_forever()
