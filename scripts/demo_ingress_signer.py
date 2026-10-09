"""Private loopback NGINX auth_request signer. Never expose port 8502 publicly.

NGINX supplies its TCP peer's IP, overwrites all client assertions, and strips
these headers from responses. This is not a general public identity endpoint.
"""
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portfolio_analytics.security.demo_quota import sign_ip, configuration
from portfolio_analytics.security.ai_access import AIUnavailable


class Signer(BaseHTTPRequestHandler):
    timeout = 2

    def do_GET(self):
        try:
            if self.path != '/sign' or self.client_address[0] != '127.0.0.1':
                raise ValueError()
            values = self.headers.get_all('X-Ingress-Peer') or []
            if len(values) != 1:
                raise ValueError()
            headers = sign_ip(values[0], allow_expired=True)
            self.send_response(204)
            for name,value in headers.items():
                self.send_header(name,value)
            self.send_header('Cache-Control','no-store')
            self.end_headers()
        except (ValueError, AIUnavailable):
            self.send_response(403)
            self.end_headers()

    def log_message(self, *args):
        pass  # Never log raw peer identifiers, assertions or paths.


if __name__ == '__main__':
    configuration(allow_expired=True)
    HTTPServer(('127.0.0.1',8502),Signer).serve_forever()
