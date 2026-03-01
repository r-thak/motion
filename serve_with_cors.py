#!/usr/bin/env python3
"""Simple HTTP server with CORS support for serving static demo files."""

import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler


class CORSHandler(SimpleHTTPRequestHandler):
    """Static file handler that responds to OPTIONS and adds CORS headers."""

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Goog-FieldMask")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    server = HTTPServer(("0.0.0.0", port), CORSHandler)
    print(f"Serving HTTP with CORS on 0.0.0.0 port {port} (http://0.0.0.0:{port}/) ...")
    server.serve_forever()
