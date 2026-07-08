"""Proxy local multi-compte pour AGY — cœur du hot-swap multi-session.

Principe (validé en conditions réelles, cf. MECHANISM_AGY.md) :

- Toutes les sessions `agy` sont lancées avec
  `CLOUD_CODE_URL=http://127.0.0.1:<port>` (voir `cli-switch agy run`).
- Le proxy lit le « compte courant » dans un fichier éditable à chaud
  (`<data_dir>/agy_proxy_current`, piloté par `cli-switch agy use <compte>`).
- Pour ce compte, il mint/cache un access_token à partir du refresh_token
  stocké dans le profil cli-switch (`profiles/<compte>/agy_live_token`),
  REMPLACE le header Authorization de chaque requête, et forwarde à l'upstream
  CloudCode réel.
- Changer le compte courant ⇒ toutes les sessions `agy` vivantes basculent à
  leur requête suivante. Aucun redémarrage.

Le token OAuth propre d'`agy` (celui du fichier ~/.gemini) devient sans
importance pour le quota : c'est le proxy qui décide, à chaque requête, quel
compte est réellement utilisé.
"""

from __future__ import annotations

import gzip
import http.client
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cli_switch.store import Store

# client_id/secret Antigravity — ceux du client « installé » d'Antigravity
# (présents dans le binaire `agy` et dans AntigravityManager), nécessaires pour
# échanger un refresh_token contre un access_token frais.
#
# NON committés (le secret-scanning de GitHub bloque les client secrets Google,
# et par principe on ne versionne pas de credential). Chargés depuis, dans l'ordre :
#   1. les variables d'env AGY_OAUTH_CLIENT_ID / AGY_OAUTH_CLIENT_SECRET ;
#   2. le module local non suivi `_oauth_client.py` (voir `_oauth_client.py.example`).
# Les extraire du binaire `agy` si besoin.
import os

CLIENT_ID = os.environ.get("AGY_OAUTH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("AGY_OAUTH_CLIENT_SECRET", "")
if not CLIENT_ID or not CLIENT_SECRET:
    try:
        from cli_switch.agy._oauth_client import (  # type: ignore
            CLIENT_ID as _CID,
            CLIENT_SECRET as _CSEC,
        )

        CLIENT_ID = CLIENT_ID or _CID
        CLIENT_SECRET = CLIENT_SECRET or _CSEC
    except ImportError:
        pass  # le proxy lèvera une erreur claire à l'usage si toujours vide

TOKEN_URL = "https://oauth2.googleapis.com/token"

# Upstream CloudCode réel. `daily-cloudcode-pa` est ce qu'utilisent les comptes
# observés sur cette machine (défaut) ; `cloudcode-pa` est la variante « prod »
# générale, utilisée en repli si le host primaire est injoignable (erreur
# réseau/DNS — PAS sur une erreur HTTP, qui est une vraie réponse du bon host).
DEFAULT_UPSTREAM = "daily-cloudcode-pa.googleapis.com"
FALLBACK_UPSTREAM = "cloudcode-pa.googleapis.com"

DEFAULT_PORT = 8899

_EMAIL_RE = re.compile(rb"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9.-]+")
_HOP_BY_HOP = {
    "host", "content-length", "connection", "proxy-connection",
    "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "authorization",
}

logger = logging.getLogger(__name__)


class AgyProxy:
    """Serveur proxy. Une instance = un port."""

    def __init__(self, store: Store, upstream: str = DEFAULT_UPSTREAM) -> None:
        self.store = store
        self.upstream = upstream
        self._token_cache: dict[str, tuple[str, float]] = {}
        self._cache_lock = threading.Lock()

    # -- état « compte courant » -------------------------------------------

    @property
    def current_file(self) -> Path:
        return self.store.data_dir / "agy_proxy_current"

    def current_account(self) -> str | None:
        try:
            return self.current_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    # -- gestion des tokens -------------------------------------------------

    def _refresh_token_for(self, account: str) -> str | None:
        p = self.store.profile(account).agy_live_token
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data.get("token", {}).get("refresh_token")

    def access_token_for(self, account: str) -> str | None:
        """Mint/cache un access_token pour le compte (thread-safe)."""
        now = time.time()
        with self._cache_lock:
            cached = self._token_cache.get(account)
            if cached and cached[1] - 60 > now:
                return cached[0]
        rt = self._refresh_token_for(account)
        if not rt:
            logger.warning("Pas de refresh_token pour le compte '%s'", account)
            return None
        if not CLIENT_ID or not CLIENT_SECRET:
            logger.error(
                "Client OAuth Antigravity absent : exporte AGY_OAUTH_CLIENT_ID/"
                "AGY_OAUTH_CLIENT_SECRET ou crée src/cli_switch/agy/_oauth_client.py "
                "(cf. _oauth_client.py.example)."
            )
            return None
        body = urllib.parse.urlencode({
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
            "refresh_token": rt, "grant_type": "refresh_token",
        }).encode()
        try:
            resp = json.load(urllib.request.urlopen(
                urllib.request.Request(TOKEN_URL, data=body), timeout=30))
        except Exception as e:  # noqa: BLE001 - réseau best-effort
            logger.warning("Échec du mint pour '%s': %s", account, e)
            return None
        at = resp.get("access_token")
        exp = now + int(resp.get("expires_in", 3600))
        with self._cache_lock:
            self._token_cache[account] = (at, exp)
        return at

    # -- serveur ------------------------------------------------------------

    def serve(self, port: int = DEFAULT_PORT) -> None:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _read_body(self) -> bytes:
                length = int(self.headers.get("Content-Length", 0) or 0)
                if length:
                    return self.rfile.read(length)
                te = (self.headers.get("Transfer-Encoding", "") or "").lower()
                if "chunked" in te:
                    buf = bytearray()
                    while True:
                        line = self.rfile.readline().strip()
                        if b";" in line:
                            line = line.split(b";", 1)[0]
                        try:
                            size = int(line, 16)
                        except ValueError:
                            break
                        if size == 0:
                            self.rfile.readline()
                            break
                        buf += self.rfile.read(size)
                        self.rfile.readline()
                    return bytes(buf)
                return b""

            def _relay(self, method: str) -> None:
                body = self._read_body()
                account = proxy.current_account()
                token = proxy.access_token_for(account) if account else None

                # Aucun token disponible (pas de compte courant, ou mint
                # échoué) : on refuse proprement plutôt que de forwarder sans
                # auth (ce qui donnerait un 401/403 opaque côté agy).
                if not token:
                    logger.warning("Aucun token pour le compte courant '%s' — requête refusée.", account)
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    msg = {"error": {"code": 503, "message":
                           f"cli-switch proxy: aucun token pour le compte '{account}'. "
                           "Vérifie `cli-switch agy use <compte>` et le refresh_token du profil."}}
                    try:
                        self.wfile.write(json.dumps(msg).encode())
                    except Exception:  # noqa: BLE001
                        pass
                    return

                out = {k: v for k, v in self.headers.items()
                       if k.lower() not in _HOP_BY_HOP}
                out["Authorization"] = f"Bearer {token}"

                def send(host, headers):
                    h = dict(headers)
                    h["Host"] = host
                    conn = http.client.HTTPSConnection(host, timeout=120)
                    conn.request(method, self.path, body=body, headers=h)
                    return conn, conn.getresponse()

                try:
                    try:
                        conn, resp = send(proxy.upstream, out)
                    except (OSError, http.client.HTTPException) as net_err:
                        # Erreur RÉSEAU (DNS/refus) sur le host primaire → repli
                        # vers l'upstream alternatif. Une erreur HTTP (4xx/5xx)
                        # n'est PAS concernée : c'est une vraie réponse.
                        if proxy.upstream != FALLBACK_UPSTREAM:
                            logger.warning("Upstream %s injoignable (%s) → repli sur %s",
                                           proxy.upstream, net_err, FALLBACK_UPSTREAM)
                            conn, resp = send(FALLBACK_UPSTREAM, out)
                        else:
                            raise
                    # Cross-compte : projet de l'ancien compte + token du
                    # nouveau ⇒ 403. Retry sans x-goog-user-project.
                    if resp.status == 403 and any(k.lower() == "x-goog-user-project" for k in out):
                        resp.read(); conn.close()
                        out2 = {k: v for k, v in out.items() if k.lower() != "x-goog-user-project"}
                        conn, resp = send(proxy.upstream, out2)
                except Exception as e:  # noqa: BLE001
                    self.send_response(502); self.end_headers()
                    self.wfile.write(f"proxy error: {e}".encode())
                    return

                # Observabilité : pour les endpoints d'identité, on lit la
                # réponse pour tracer le compte RÉELLEMENT vu par l'upstream
                # (= celui du token injecté). Permet de vérifier le hot-swap.
                probe = any(x in self.path for x in
                            ("fetchUserInfo", "retrieveUserQuotaSummary", "loadCodeAssist"))
                if probe:
                    raw = resp.read()
                    seen = ""
                    try:
                        txt = raw
                        if "gzip" in (resp.getheader("Content-Encoding") or "").lower():
                            txt = gzip.decompress(raw)
                        emails = sorted({m.decode() for m in _EMAIL_RE.findall(txt)})
                        if emails:
                            seen = " ".join(emails)
                    except Exception:  # noqa: BLE001
                        pass
                    logger.info("%s compte=%s upstream_voit=%s [%s]",
                                self.path.split(":")[-1][:24], account, seen or "?", resp.status)
                    headers_out = [(k, v) for k, v in resp.getheaders()
                                   if k.lower() not in {"connection", "transfer-encoding",
                                                        "content-length", "keep-alive"}]
                    self.send_response(resp.status)
                    for k, v in headers_out:
                        self.send_header(k, v)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    try:
                        self.wfile.write(raw)
                    except Exception:  # noqa: BLE001
                        pass
                    conn.close()
                    return

                headers_out = [(k, v) for k, v in resp.getheaders()
                               if k.lower() not in {"connection", "transfer-encoding",
                                                    "content-length", "keep-alive"}]
                self.send_response(resp.status)
                for k, v in headers_out:
                    self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except Exception:  # noqa: BLE001 - client parti
                    pass
                finally:
                    conn.close()

            def do_GET(self):
                self._relay("GET")

            def do_POST(self):
                self._relay("POST")

            def log_message(self, *a):
                pass

        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        logger.info("Proxy AGY en écoute sur 127.0.0.1:%d → %s (compte=%s)",
                    port, self.upstream, self.current_account())
        httpd.serve_forever()


def main() -> None:
    import argparse
    import os

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser(description="Proxy AGY multi-compte")
    ap.add_argument("--port", type=int, default=int(os.environ.get("CLI_SWITCH_AGY_PROXY_PORT", DEFAULT_PORT)))
    ap.add_argument("--upstream", default=os.environ.get("CLI_SWITCH_AGY_UPSTREAM", DEFAULT_UPSTREAM))
    args = ap.parse_args()
    AgyProxy(Store(), upstream=args.upstream).serve(port=args.port)


if __name__ == "__main__":
    main()
