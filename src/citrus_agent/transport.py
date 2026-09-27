from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.request

from .config import validate_hub


class HubError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HubError(code, "Hub redirects are refused; configure its canonical URL")


class HubClient:
    def __init__(self, url: str, token: str | None = None, ca_file: str | None = None):
        self.url = validate_hub(url)
        self.token = token
        context = ssl.create_default_context(cafile=ca_file)
        self.opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPSHandler(context=context)
        )

    def request(self, path: str, body: dict, timeout: float = 35) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.url + path, data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                content = response.read(2 * 1024 * 1024 + 1)
                if len(content) > 2 * 1024 * 1024:
                    raise HubError(0, "Hub response is too large")
                parsed = json.loads(content) if content else {}
                if not isinstance(parsed, dict):
                    raise HubError(0, "Hub response must be an object")
                return parsed
        except urllib.error.HTTPError as exc:
            # Never copy response bodies or request headers into logs.
            raise HubError(exc.code, f"Hub returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise HubError(0, f"Hub transport failure: {type(exc).__name__}") from None

    async def post(self, path: str, body: dict, timeout: float = 35) -> dict:
        return await asyncio.to_thread(self.request, path, body, timeout)
