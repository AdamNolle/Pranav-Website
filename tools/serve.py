"""
Local dev server: python3 tools/serve.py [port]

The stock `python3 -m http.server` only queues 5 pending connections, so the page's parallel
texture/model requests get dropped. This one queues 256, serves correct MIME types for
.glb/.wasm/.webp/.woff2, and disables caching so edits show up on reload.
"""
import http.server, os, socketserver, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      '.glb': 'model/gltf-binary', '.wasm': 'application/wasm',
                      '.webp': 'image/webp', '.woff2': 'font/woff2', '.js': 'text/javascript'}

    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def log_message(self, *a):
        pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 256


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f'Serving {ROOT} at http://127.0.0.1:{port}/')
    Server(('127.0.0.1', port), Handler).serve_forever()
