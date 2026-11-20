"""Dev-only static server for the Lunapark prototype.

Open the printed port in the Arena live preview; "/" redirects straight to the
lab page so the prototype starts without a directory listing.

    python3 tools/serve_preview.py [port]
"""
import functools
import http.server
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8100
PAGE = "/lunapark-discovery.html"


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", PAGE)
            self.end_headers()
            return
        super().do_GET()


if __name__ == "__main__":
    handler = functools.partial(Handler, directory=str(ROOT))
    with http.server.ThreadingHTTPServer(("0.0.0.0", PORT), handler) as httpd:
        print(f"Lunapark preview: http://0.0.0.0:{PORT}{PAGE}")
        httpd.serve_forever()
