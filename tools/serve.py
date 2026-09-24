"""
Local dev server: python3 tools/serve.py [port]

The stock `python3 -m http.server` only queues 5 pending connections, so the page's parallel
texture/model requests get dropped. This one queues 256, serves correct MIME types for
.glb/.wasm/.webp/.woff2, and disables caching so edits show up on reload. It also gzips text,
scripts, wasm and models the way GitHub Pages does, so local load timings match production.
"""
import gzip, http.server, os, socketserver, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GZIP = {'.html', '.css', '.js', '.mjs', '.json', '.svg', '.wasm', '.glb', '.ico', '.txt', '.webmanifest', '.md'}
_cache = {}


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      '.glb': 'model/gltf-binary', '.wasm': 'application/wasm',
                      '.webp': 'image/webp', '.woff2': 'font/woff2', '.js': 'text/javascript'}

    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            path = os.path.join(path, 'index.html')
        ext = os.path.splitext(path)[1]
        if ext not in GZIP or 'gzip' not in self.headers.get('Accept-Encoding', '') or not os.path.isfile(path):
            return super().send_head()
        key = (path, os.path.getmtime(path))
        if key not in _cache:
            with open(path, 'rb') as f:
                _cache[key] = gzip.compress(f.read(), 6)
        body = _cache[key]
        self.send_response(200)
        self.send_header('Content-Type', self.guess_type(path))
        self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        return _Body(body)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def log_message(self, *a):
        pass


class _Body:
    def __init__(self, b): self.b = b
    def read(self, *a): b, self.b = self.b, b''; return b
    def close(self): pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 256


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f'Serving {ROOT} at http://127.0.0.1:{port}/')
    Server(('127.0.0.1', port), Handler).serve_forever()
