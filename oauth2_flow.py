#!/usr/bin/env python3
"""
oauth2_flow.py — One-time OAuth2 authorization to get X access and refresh tokens.

Run this once to authorize the app against your X account.
Copy the printed tokens into your .env file.

Usage:
  python3 oauth2_flow.py
"""

import os
import re
import sys
import json
import base64
import secrets
import hashlib
import webbrowser
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT          = 3000
CALLBACK_URL  = f"http://localhost:{PORT}/callback"
AUTH_URL      = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL     = "https://api.twitter.com/2/oauth2/token"
SCOPES        = "tweet.read users.read bookmark.read offline.access"
ENV_FILE      = "./.env"

_callback_code = None
_callback_state = None


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _callback_code, _callback_state
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        _callback_code  = params.get("code", [None])[0]
        _callback_state = params.get("state", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Authorization complete. You can close this tab.</h2>")

    def log_message(self, *args):
        pass  # silence request logs


def load_env():
    env = {}
    if not os.path.exists(ENV_FILE):
        return env
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main():
    env = load_env()
    client_id     = env.get("TWITTER_CLIENT_ID") or os.environ.get("TWITTER_CLIENT_ID", "")
    client_secret = env.get("TWITTER_CLIENT_SECRET") or os.environ.get("TWITTER_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        print("ERROR: TWITTER_CLIENT_ID and TWITTER_CLIENT_SECRET must be set in .env")
        sys.exit(1)

    # PKCE: code verifier + challenge
    code_verifier  = secrets.token_urlsafe(64)
    digest         = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    state          = secrets.token_urlsafe(16)

    params = urllib.parse.urlencode({
        "response_type":         "code",
        "client_id":             client_id,
        "redirect_uri":          CALLBACK_URL,
        "scope":                 SCOPES,
        "state":                 state,
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
    })
    auth_link = f"{AUTH_URL}?{params}"

    print("\nOpening authorization URL in your browser...")
    print(f"\n{auth_link}\n")
    webbrowser.open(auth_link)

    print(f"Waiting for callback on http://localhost:{PORT}/callback ...")
    server = HTTPServer(("localhost", PORT), CallbackHandler)
    server.handle_request()  # handles exactly one request then stops

    if not _callback_code:
        print("ERROR: No authorization code received.")
        sys.exit(1)

    if _callback_state != state:
        print("ERROR: State mismatch — possible CSRF. Aborting.")
        sys.exit(1)

    print("Authorization code received. Exchanging for tokens...")

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = urllib.parse.urlencode({
        "code":          _callback_code,
        "grant_type":    "authorization_code",
        "redirect_uri":  CALLBACK_URL,
        "code_verifier": code_verifier,
    }).encode()

    req = urllib.request.Request(
        TOKEN_URL, data=data,
        headers={
            "Authorization":  f"Basic {credentials}",
            "Content-Type":   "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read().decode())
    except Exception as e:
        print(f"ERROR exchanging code for tokens: {e}")
        sys.exit(1)

    access_token  = result.get("access_token", "")
    refresh_token = result.get("refresh_token", "")

    if not access_token:
        print(f"ERROR: No access token in response: {result}")
        sys.exit(1)

    print("\n--- Tokens received ---")
    print(f"TWITTER_OAUTH2_ACCESS_TOKEN={access_token}")
    print(f"TWITTER_OAUTH2_REFRESH_TOKEN={refresh_token}")
    print("\nAdd these to your .env file.")


if __name__ == "__main__":
    main()
