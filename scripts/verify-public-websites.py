#!/usr/bin/env python3
"""Read-only DEV-26 redirect/TLS/header checks, with no credentials or cookies."""
import concurrent.futures
import json
import ssl
import urllib.error
import urllib.request

HOSTS = ("wodiq.nl", "www.wodiq.nl", "openairco.tuinstra.dev",
         "marcel.tuinstra.dev", "tuinstra.dev")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify(host):
    opener = urllib.request.build_opener(
        NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    results = {}
    for scheme in ("http", "https"):
        url = f"{scheme}://{host}/"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "DEV-26 public route verification"})
            try:
                response = opener.open(request, timeout=15)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                headers = response.headers
                if scheme == "http":
                    checks = {"redirect_status": response.code in (301, 308),
                              "https_destination": headers.get("Location") == f"https://{host}/"}
                else:
                    checks = {"status": response.code == 200,
                              "hsts": "max-age=31536000" in headers.get("Strict-Transport-Security", ""),
                              "nosniff": headers.get("X-Content-Type-Options") == "nosniff",
                              "referrer_policy": headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"}
                results[scheme] = {"status": response.code, "checks": checks,
                                   "pass": all(checks.values())}
        except Exception as error:
            results[scheme] = {"pass": False, "error": type(error).__name__}
    return {"host": host, "checks": results, "pass": all(x["pass"] for x in results.values())}


if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        rows = list(pool.map(verify, HOSTS))
    print(json.dumps({"sites": rows, "pass": all(r["pass"] for r in rows)}, indent=2))
    raise SystemExit(0 if all(r["pass"] for r in rows) else 1)
