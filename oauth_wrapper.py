#!/usr/bin/env python3

import os
import sys
import json
import time
import uuid
import secrets
import hashlib
import base64
import logging
import urllib.request
import urllib.parse
import urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ============================================================
# OAuth MCP Wrapper
#
# Public:
#   https://mcp.domain-kamu.com/mcp
#
# Cloudflare -> localhost:8081
# Wrapper    -> localhost:8082
# MCP        -> localhost:8082
# ============================================================

# Dibaca dari file config — di-set otomatis oleh install.sh
_url_file = Path.home() / ".config" / "termux-mcp" / "public-url"
PUBLIC_BASE_URL = _url_file.read_text().strip() if _url_file.exists() else "https://mcp.example.com"

WRAPPER_HOST = "127.0.0.1"
WRAPPER_PORT = 8081

MCP_HOST = "127.0.0.1"
MCP_PORT = 8082

BASE_DIR = Path.home() / "termux-mcp"
LOG_DIR = BASE_DIR / "logs"
CONFIG_DIR = Path.home() / ".config" / "termux-mcp-oauth"

TOKEN_FILE = Path.home() / ".config" / "termux-mcp" / "token"

CLIENT_FILE = CONFIG_DIR / "clients.json"
AUTH_CODE_FILE = CONFIG_DIR / "auth_codes.json"
TOKEN_DB = CONFIG_DIR / "tokens.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "oauth.log"),
    ],
)

log = logging.getLogger("oauth-mcp")


# ============================================================
# TOKEN MCP
# ============================================================

def get_mcp_token():
    token = os.environ.get("MCP_BEARER_TOKEN", "").strip()

    if token:
        return token

    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text().strip()
        if token:
            return token

    log.error("MCP token tidak ditemukan: %s", TOKEN_FILE)
    sys.exit(1)


MCP_BEARER_TOKEN = get_mcp_token()


# ============================================================
# JSON STORAGE
# ============================================================

def load_json(path):
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception as e:
        log.warning("Gagal membaca %s: %s", path, e)

    return {}


def save_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


# ============================================================
# PKCE
# ============================================================

def verify_pkce(verifier, challenge, method):
    if not challenge:
        return True

    if not verifier:
        return False

    if method == "S256":
        digest = hashlib.sha256(verifier.encode()).digest()
        calculated = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        return secrets.compare_digest(calculated, challenge)

    if method == "plain":
        return secrets.compare_digest(verifier, challenge)

    return False


# ============================================================
# CLIENT
# ============================================================

def register_client(client_id, redirect_uris):
    clients = load_json(CLIENT_FILE)

    client_secret = secrets.token_urlsafe(32)

    clients[client_id] = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uris": redirect_uris,
        "created_at": int(time.time()),
    }

    save_json(CLIENT_FILE, clients)

    return clients[client_id]


# ============================================================
# AUTH CODE
# ============================================================

def create_auth_code(params):
    codes = load_json(AUTH_CODE_FILE)

    code = secrets.token_urlsafe(32)

    codes[code] = {
        "client_id": params.get("client_id"),
        "redirect_uri": params.get("redirect_uri"),
        "code_challenge": params.get("code_challenge"),
        "code_challenge_method": params.get("code_challenge_method", "plain"),
        "scope": params.get("scope", "mcp"),
        "created_at": int(time.time()),
        "expires_in": 600,
    }

    save_json(AUTH_CODE_FILE, codes)

    return code


def consume_auth_code(code):
    codes = load_json(AUTH_CODE_FILE)

    entry = codes.pop(code, None)

    if entry is None:
        return None

    save_json(AUTH_CODE_FILE, codes)

    age = int(time.time()) - entry["created_at"]

    if age > entry.get("expires_in", 600):
        return None

    return entry


# ============================================================
# ACCESS / REFRESH TOKEN
# ============================================================

def create_tokens(client_id, scope="mcp"):
    tokens = load_json(TOKEN_DB)

    access_token = secrets.token_urlsafe(48)
    refresh_token = secrets.token_urlsafe(48)

    now = int(time.time())

    tokens[access_token] = {
        "type": "access",
        "client_id": client_id,
        "scope": scope,
        "created_at": now,
        "expires_in": 3600,
    }

    tokens[refresh_token] = {
        "type": "refresh",
        "client_id": client_id,
        "scope": scope,
        "created_at": now,
        "expires_in": 60 * 60 * 24 * 365,
    }

    save_json(TOKEN_DB, tokens)

    return access_token, refresh_token


def refresh_access_token(refresh_token):
    tokens = load_json(TOKEN_DB)

    entry = tokens.get(refresh_token)

    if not entry or entry.get("type") != "refresh":
        return None

    age = int(time.time()) - entry["created_at"]

    if age > entry.get("expires_in", 31536000):
        return None

    access_token = secrets.token_urlsafe(48)

    tokens[access_token] = {
        "type": "access",
        "client_id": entry["client_id"],
        "scope": entry.get("scope", "mcp"),
        "created_at": int(time.time()),
        "expires_in": 3600,
    }

    save_json(TOKEN_DB, tokens)

    return access_token


def validate_access_token(token):
    tokens = load_json(TOKEN_DB)

    entry = tokens.get(token)

    if not entry or entry.get("type") != "access":
        return False

    age = int(time.time()) - entry["created_at"]

    return age < entry.get("expires_in", 3600)


# ============================================================
# MCP FORWARDER
# ============================================================

def forward_to_mcp(method, path, headers, body):

    url = f"http://{MCP_HOST}:{MCP_PORT}{path}"

    forward_headers = {
        "Authorization": f"Bearer {MCP_BEARER_TOKEN}",
        "Content-Type": headers.get(
            "content-type",
            "application/json"
        ),
        "Accept": headers.get(
            "accept",
            "application/json, text/event-stream"
        ),
    }

    for h in (
        "mcp-session-id",
        "last-event-id",
    ):
        if h in headers:
            forward_headers[h] = headers[h]

    req = urllib.request.Request(
        url,
        data=body if body else None,
        headers=forward_headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as response:

            response_body = response.read()

            response_headers = {
                k.lower(): v
                for k, v in response.headers.items()
            }

            return (
                response.status,
                response_headers,
                response_body,
            )

    except urllib.error.HTTPError as e:

        response_body = e.read()

        response_headers = {
            k.lower(): v
            for k, v in e.headers.items()
        }

        return (
            e.code,
            response_headers,
            response_body,
        )

    except Exception as e:

        log.error(
            "MCP forward error: %s",
            e,
        )

        return (
            502,
            {
                "content-type": "application/json"
            },
            json.dumps({
                "error": "bad_gateway",
                "detail": str(e),
            }).encode(),
        )


# ============================================================
# HTTP HANDLER
# ============================================================

class Handler(BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.info(
            "%s - %s",
            self.client_address[0],
            fmt % args,
        )

    def path_only(self):
        return urllib.parse.urlparse(
            self.path
        ).path

    def query(self):
        parsed = urllib.parse.urlparse(self.path)

        return dict(
            urllib.parse.parse_qsl(
                parsed.query
            )
        )

    def read_body(self):
        length = int(
            self.headers.get(
                "Content-Length",
                "0"
            )
        )

        return (
            self.rfile.read(length)
            if length
            else b""
        )

    def send_json(self, status, data, headers=None):

        body = json.dumps(
            data,
            separators=(",", ":")
        ).encode()

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        if headers:
            for k, v in headers.items():
                self.send_header(k, v)

        self.end_headers()

        self.wfile.write(body)

    def redirect(self, location):

        self.send_response(302)

        self.send_header(
            "Location",
            location
        )

        self.send_header(
            "Cache-Control",
            "no-store"
        )

        self.send_header(
            "Content-Length",
            "0"
        )

        self.end_headers()

    # ========================================================
    # OPTIONS
    # ========================================================

    def do_OPTIONS(self):

        self.send_response(204)

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, DELETE, OPTIONS"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Authorization, Content-Type, Accept, Mcp-Session-Id, Last-Event-Id"
        )

        self.send_header(
            "Access-Control-Expose-Headers",
            "Mcp-Session-Id"
        )

        self.send_header(
            "Content-Length",
            "0"
        )

        self.end_headers()

    # ========================================================
    # GET
    # ========================================================

    def do_GET(self):

        path = self.path_only()

        if path == "/health":
            return self.send_json(
                200,
                {
                    "status": "ok",
                    "service": "oauth-mcp-wrapper",
                    "wrapper_port": WRAPPER_PORT,
                    "mcp_backend": f"{MCP_HOST}:{MCP_PORT}",
                }
            )

        # MCP Protected Resource Metadata
        if path == "/.well-known/oauth-protected-resource":

            return self.send_json(
                200,
                {
                    "resource": f"{PUBLIC_BASE_URL}/mcp",
                    "authorization_servers": [
                        PUBLIC_BASE_URL
                    ],
                    "scopes_supported": [
                        "mcp"
                    ],
                    "bearer_methods_supported": [
                        "header"
                    ]
                }
            )

        # OAuth Authorization Server Metadata
        if path == "/.well-known/oauth-authorization-server":

            return self.send_json(
                200,
                {
                    "issuer": PUBLIC_BASE_URL,
                    "authorization_endpoint":
                        f"{PUBLIC_BASE_URL}/oauth/authorize",
                    "token_endpoint":
                        f"{PUBLIC_BASE_URL}/oauth/token",
                    "registration_endpoint":
                        f"{PUBLIC_BASE_URL}/oauth/register",

                    "response_types_supported": [
                        "code"
                    ],

                    "grant_types_supported": [
                        "authorization_code",
                        "refresh_token"
                    ],

                    "token_endpoint_auth_methods_supported": [
                        "client_secret_post",
                        "client_secret_basic",
                        "none"
                    ],

                    "code_challenge_methods_supported": [
                        "S256"
                    ],

                    "scopes_supported": [
                        "mcp",
                        "offline_access"
                    ],

                    "client_id_metadata_document_supported": False
                }
            )

        # OIDC discovery compatibility
        if path == "/.well-known/openid-configuration":

            return self.send_json(
                200,
                {
                    "issuer": PUBLIC_BASE_URL,
                    "authorization_endpoint":
                        f"{PUBLIC_BASE_URL}/oauth/authorize",
                    "token_endpoint":
                        f"{PUBLIC_BASE_URL}/oauth/token",
                    "response_types_supported": [
                        "code"
                    ],
                    "scopes_supported": [
                        "openid",
                        "mcp",
                        "offline_access"
                    ]
                }
            )

        # OAuth authorize
        if path == "/oauth/authorize":

            return self.handle_authorize()

        # OAuth info
        if path == "/oauth/info":

            clients = load_json(CLIENT_FILE)
            tokens = load_json(TOKEN_DB)

            return self.send_json(
                200,
                {
                    "service": "oauth-mcp-wrapper",
                    "clients": len(clients),
                    "tokens": len(tokens),
                    "mcp_backend":
                        f"http://{MCP_HOST}:{MCP_PORT}",
                    "public":
                        PUBLIC_BASE_URL,
                }
            )

        # Everything else = MCP
        return self.forward_mcp_request("GET")

    # ========================================================
    # POST
    # ========================================================

    def do_POST(self):

        path = self.path_only()

        if path == "/oauth/register":
            return self.handle_register()

        if path == "/oauth/token":
            return self.handle_token()

        return self.forward_mcp_request("POST")

    # ========================================================
    # DELETE
    # ========================================================

    def do_DELETE(self):

        return self.forward_mcp_request("DELETE")

    # ========================================================
    # REGISTER
    # ========================================================

    def handle_register(self):

        body = self.read_body()

        try:
            data = json.loads(
                body.decode()
            ) if body else {}
        except Exception:
            data = {}

        client_id = (
            data.get("client_id")
            or f"mcp-client-{secrets.token_hex(8)}"
        )

        redirect_uris = data.get(
            "redirect_uris",
            []
        )

        client = register_client(
            client_id,
            redirect_uris
        )

        log.info(
            "OAuth client registered: %s",
            client_id
        )

        return self.send_json(
            201,
            {
                "client_id":
                    client["client_id"],

                "client_secret":
                    client["client_secret"],

                "client_id_issued_at":
                    client["created_at"],

                "client_secret_expires_at":
                    0,

                "redirect_uris":
                    redirect_uris,

                "grant_types": [
                    "authorization_code",
                    "refresh_token"
                ],

                "response_types": [
                    "code"
                ],

                "token_endpoint_auth_method":
                    "client_secret_post"
            }
        )

    # ========================================================
    # AUTHORIZE
    # ========================================================

    def handle_authorize(self):

        params = self.query()

        client_id = params.get(
            "client_id",
            ""
        )

        redirect_uri = params.get(
            "redirect_uri",
            ""
        )

        state = params.get(
            "state",
            ""
        )

        code_challenge = params.get(
            "code_challenge",
            ""
        )

        code_challenge_method = params.get(
            "code_challenge_method",
            "plain"
        )

        if not client_id or not redirect_uri:

            return self.send_json(
                400,
                {
                    "error":
                        "invalid_request",
                    "error_description":
                        "client_id dan redirect_uri wajib"
                }
            )

        clients = load_json(
            CLIENT_FILE
        )

        client = clients.get(
            client_id
        )

        # Untuk client yang belum terdaftar,
        # buat registry entry.
        if not client:

            client = register_client(
                client_id,
                [redirect_uri]
            )

        # Validasi redirect URI
        registered = client.get(
            "redirect_uris",
            []
        )

        if registered and redirect_uri not in registered:

            return self.send_json(
                400,
                {
                    "error":
                        "invalid_request",
                    "error_description":
                        "redirect_uri tidak terdaftar"
                }
            )

        if (
            code_challenge
            and code_challenge_method != "S256"
        ):

            return self.send_json(
                400,
                {
                    "error":
                        "invalid_request",
                    "error_description":
                        "Hanya PKCE S256 yang didukung"
                }
            )

        code = create_auth_code(
            params
        )

        location = (
            redirect_uri
            + ("&" if "?" in redirect_uri else "?")
            + "code="
            + urllib.parse.quote(code)
        )

        if state:

            location += (
                "&state="
                + urllib.parse.quote(state)
            )

        log.info(
            "OAuth authorization: client=%s",
            client_id
        )

        return self.redirect(
            location
        )

    # ========================================================
    # TOKEN
    # ========================================================

    def handle_token(self):

        body = self.read_body()

        content_type = (
            self.headers.get(
                "Content-Type",
                ""
            )
        )

        if "application/json" in content_type:

            try:
                params = json.loads(
                    body.decode()
                )
            except Exception:
                params = {}

        else:

            params = dict(
                urllib.parse.parse_qsl(
                    body.decode()
                )
            )

        # Basic authentication
        auth = self.headers.get(
            "Authorization",
            ""
        )

        if auth.startswith("Basic "):

            try:

                decoded = base64.b64decode(
                    auth[6:]
                ).decode()

                client_id, client_secret = \
                    decoded.split(":", 1)

                params.setdefault(
                    "client_id",
                    client_id
                )

                params.setdefault(
                    "client_secret",
                    client_secret
                )

            except Exception:
                pass

        grant_type = params.get(
            "grant_type",
            ""
        )

        client_id = params.get(
            "client_id",
            ""
        )

        clients = load_json(
            CLIENT_FILE
        )

        client = clients.get(
            client_id
        )

        if grant_type == "authorization_code":

            code = params.get(
                "code",
                ""
            )

            entry = consume_auth_code(
                code
            )

            if not entry:

                return self.send_json(
                    400,
                    {
                        "error":
                            "invalid_grant"
                    }
                )

            client_id = entry["client_id"]

            if (
                params.get(
                    "redirect_uri"
                )
                and params.get(
                    "redirect_uri"
                ) != entry["redirect_uri"]
            ):

                return self.send_json(
                    400,
                    {
                        "error":
                            "invalid_grant"
                    }
                )

            if not verify_pkce(
                params.get(
                    "code_verifier"
                ),
                entry.get(
                    "code_challenge"
                ),
                entry.get(
                    "code_challenge_method"
                )
            ):

                return self.send_json(
                    400,
                    {
                        "error":
                            "invalid_grant",
                        "error_description":
                            "PKCE verification failed"
                    }
                )

        elif grant_type == "refresh_token":

            refresh_token = params.get(
                "refresh_token",
                ""
            )

            access_token = refresh_access_token(
                refresh_token
            )

            if not access_token:

                return self.send_json(
                    400,
                    {
                        "error":
                            "invalid_grant"
                    }
                )

            return self.send_json(
                200,
                {
                    "access_token":
                        access_token,
                    "token_type":
                        "Bearer",
                    "expires_in":
                        3600,
                    "scope":
                        "mcp",
                }
            )

        else:

            return self.send_json(
                400,
                {
                    "error":
                        "unsupported_grant_type"
                }
            )

        access_token, refresh_token = \
            create_tokens(client_id)

        log.info(
            "OAuth token issued: client=%s",
            client_id
        )

        return self.send_json(
            200,
            {
                "access_token":
                    access_token,

                "token_type":
                    "Bearer",

                "expires_in":
                    3600,

                "refresh_token":
                    refresh_token,

                "refresh_token_expires_in":
                    31536000,

                "scope":
                    "mcp"
            }
        )

    # ========================================================
    # MCP
    # ========================================================

    def forward_mcp_request(self, method):

        path = self.path_only()

        auth = self.headers.get(
            "Authorization",
            ""
        )

        if not auth.startswith(
            "Bearer "
        ):

            return self.send_json(
                401,
                {
                    "error":
                        "unauthorized"
                },
                {
                    "WWW-Authenticate":
                        f'Bearer resource_metadata="{PUBLIC_BASE_URL}/.well-known/oauth-protected-resource"'
                }
            )

        oauth_token = auth[7:]

        if not validate_access_token(
            oauth_token
        ):

            return self.send_json(
                401,
                {
                    "error":
                        "invalid_token"
                },
                {
                    "WWW-Authenticate":
                        f'Bearer resource_metadata="{PUBLIC_BASE_URL}/.well-known/oauth-protected-resource"'
                }
            )

        body = self.read_body()

        headers = {
            k.lower(): v
            for k, v in self.headers.items()
        }

        # DEBUG: log request dari AI client
        try:
            body_preview = body[:500].decode("utf-8", errors="replace") if body else "(empty)"
            parsed_body = json.loads(body) if body else {}
            method_name = parsed_body.get("method", "?")
            log.info(">>> CLIENT REQUEST: method=%s path=%s body_preview=%s", method_name, path, body_preview)
        except Exception:
            pass

        status, response_headers, response_body = \
            forward_to_mcp(
                method,
                path,
                headers,
                body
            )

        # Patch protocolVersion di response initialize agar cocok dengan client
        try:
            if body:
                req_json = json.loads(body)
                if req_json.get("method") == "initialize" and status == 200:
                    resp_json = json.loads(response_body)
                    client_version = json.loads(body).get("params", {}).get("protocolVersion", "")
                    if client_version and resp_json.get("result", {}).get("protocolVersion"):
                        old_ver = resp_json["result"]["protocolVersion"]
                        resp_json["result"]["protocolVersion"] = client_version
                        response_body = json.dumps(resp_json).encode()
                        log.info("PATCH protocolVersion: %s → %s", old_ver, client_version)
        except Exception as e:
            log.warning("Patch protocolVersion error: %s", e)

        # DEBUG: log response ke AI client
        try:
            resp_preview = response_body[:300].decode("utf-8", errors="replace") if response_body else "(empty)"
            log.info("<<< MCP RESPONSE: status=%s preview=%s", status, resp_preview)
        except Exception:
            pass

        self.send_response(
            status
        )

        content_type = response_headers.get(
            "content-type",
            "application/json"
        )

        self.send_header(
            "Content-Type",
            content_type
        )

        for header in (
            "mcp-session-id",
            "cache-control",
        ):

            if header in response_headers:

                self.send_header(
                    header.title(),
                    response_headers[header]
                )

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Expose-Headers",
            "Mcp-Session-Id"
        )

        self.send_header(
            "Content-Length",
            str(len(response_body))
        )

        self.end_headers()

        self.wfile.write(
            response_body
        )

        log.info(
            "MCP %s %s -> %s",
            method,
            path,
            status
        )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 64)
    print("      OAUTH MCP WRAPPER")
    print("=" * 64)
    print(
        f" Public : {PUBLIC_BASE_URL}"
    )
    print(
        f" Wrapper: {WRAPPER_HOST}:{WRAPPER_PORT}"
    )
    print(
        f" MCP    : {MCP_HOST}:{MCP_PORT}"
    )
    print(
        f" Token  : {MCP_BEARER_TOKEN[:8]}..."
    )
    print("=" * 64)
    print()

    server = ThreadingHTTPServer(
        (WRAPPER_HOST, WRAPPER_PORT),
        Handler
    )

    log.info(
        "OAuth MCP wrapper listening on %s:%s",
        WRAPPER_HOST,
        WRAPPER_PORT
    )

    try:
        server.serve_forever()

    except KeyboardInterrupt:
        pass

    finally:
        server.server_close()
