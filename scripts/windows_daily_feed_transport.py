#!/usr/bin/env python3
# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Verify cold Windows transport of the public encrypted Hongguo fixture."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fagao import daily_feeds, network
from fagao.store import Store

# Immutable public publication receipt for the 2026-11-11 Hongguo edition.
COMMIT = "1a65235a8625571354ac2bc1ca0aba1e40cd1998"
SHA256 = "891f195e9f5ea04226b6dd9c652038df31caff203915926c92497647f89fb145"
LENGTH = 10148
RAW_HOST = "raw.githubusercontent.com"
CDN_HOSTS = ("cdn.jsdelivr.net", "gcore.jsdelivr.net")
RAW_URL = f"{daily_feeds.RAW_ROOT}{COMMIT}/objects/hongguo/{SHA256}.bin"
CDN_PATH = f"/gh/ShiQi-Han03/fagao-ranking-feed@{COMMIT}/objects/hongguo/{SHA256}.bin"


def check_transport():
    attempts = []
    with tempfile.TemporaryDirectory(prefix="fagao-cold-ranking-") as directory:
        network.configure(directory)
        store = Store(directory)
        cache = daily_feeds.read_cache(store, "hongguo")
        if cache["snapshot"] is not None or cache["history"]:
            raise RuntimeError("Expected an empty Hongguo cache")

        real_build_opener = network.build_opener

        def controlled_opener(*handlers):
            opener = real_build_opener(*handlers)
            real_open = opener.open

            def open_when_available(request, timeout):
                url = urlsplit(request.full_url)
                host = url.hostname
                if request.get_method() != "GET" or request.data is not None:
                    raise RuntimeError("Unexpected request method or body")
                if any(k.lower() in ("authorization", "cookie", "proxy-authorization")
                       for k, _ in request.header_items()):
                    raise RuntimeError("Credential header on a public artifact request")
                if request.full_url == RAW_URL:
                    attempts.append(RAW_HOST)
                    raise URLError("simulated primary transport outage")
                if host not in CDN_HOSTS or request.full_url != f"https://{host}{CDN_PATH}":
                    raise RuntimeError("Unexpected fallback URL")
                attempts.append(host)
                return real_open(request, timeout=timeout)

            opener.open = open_when_available
            return opener

        with patch.object(network, "build_opener", controlled_opener):
            encrypted = daily_feeds._download(RAW_URL, LENGTH)

    if not attempts or attempts[0] != RAW_HOST or attempts[-1] not in CDN_HOSTS:
        raise RuntimeError("Expected a successful public fallback after blocking the primary host")
    digest = hashlib.sha256(encrypted).hexdigest()
    if len(encrypted) != LENGTH or digest != SHA256:
        raise RuntimeError("Encrypted public artifact differed from its publication receipt")
    return {"board": "hongguo", "source_date": "2026-11-11", "initial_cached_editions": 0,
            "platform": platform.system(), "python": platform.python_version(),
            "primary_outage_simulated": True, "attempt_hosts": attempts,
            "authenticated_headers_sent": False, "network_fallback": True,
            "byte_length": len(encrypted), "sha256": digest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Native Windows acceptance requires a Windows runner")
    receipt = check_transport()
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print("Verified the public Hongguo ciphertext over", receipt["attempt_hosts"][-1])
