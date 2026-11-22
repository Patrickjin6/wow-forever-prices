"""Minimal Blizzard Battle.net API client (standard library only)."""
import base64
import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

OAUTH_URL = "https://oauth.battle.net/token"


class ApiError(Exception):
    pass


class Api:
    def __init__(self, client_id, client_secret, region="us", locale="en_US"):
        if not client_id or not client_secret:
            raise ApiError("BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET must be set")
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region
        self.locale = locale
        self.base = f"https://{region}.api.blizzard.com"
        self._token = None
        self._token_expiry = 0

    def token(self):
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        req = urllib.request.Request(
            OAUTH_URL,
            data=b"grant_type=client_credentials",
            headers={"Authorization": f"Basic {auth}",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read())
        self._token = body["access_token"]
        self._token_expiry = time.time() + int(body.get("expires_in", 3600))
        return self._token

    def get(self, path, namespace, params=None, retries=4, timeout=180):
        """Return (json_or_None, headers). None means 404."""
        q = {"namespace": namespace, "locale": self.locale}
        if params:
            q.update(params)
        url = f"{self.base}{path}?{urllib.parse.urlencode(q)}"
        last = None
        for attempt in range(retries):
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self.token()}",
                "Accept-Encoding": "gzip",
            })
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    raw = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    return json.loads(raw), dict(r.headers)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None, dict(e.headers or {})
                if e.code == 401:
                    self._token = None
                last = e
                if e.code not in (401, 429, 500, 502, 503, 504):
                    raise
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last = e
            time.sleep(2 * (attempt + 1))
        raise ApiError(f"GET {path} failed after {retries} tries: {last}")
