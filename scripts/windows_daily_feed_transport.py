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
from windows_install_smoke import official_ci_repository

# Immutable public publication receipt for the 2026-11-11 Hongguo edition.
COMMIT = "1a65235a8625571354ac2bc1ca0aba1e40cd1998"
SHA256 = "891f195e9f5ea04226b6dd9c652038df31caff203915926c92497647f89fb145"
LENGTH = 10148
RAW_HOST = "raw.githubusercontent.com"
CDN_HOSTS = ("cdn.jsdelivr.net", "gcore.jsdelivr.net")
OBJECT = f"objects/hongguo/{SHA256}.bin"
FEED_REPOS = ("HeyFagao/fagao-ranking-feed", "ShiQi-Han03/fagao-ranking-feed")


def check_transport(repository):
    if repository not in FEED_REPOS:
        raise ValueError("Expected a known owner of the pinned feed")
    primary_urls = {f"https://{RAW_HOST}/{repo}/{COMMIT}/{OBJECT}" for repo in FEED_REPOS}
    cdn_urls = {f"https://{host}/gh/{repo}@{COMMIT}/{OBJECT}" for repo in FEED_REPOS for host in CDN_HOSTS}
    published_url = f"https://{RAW_HOST}/{repository}/{COMMIT}/{OBJECT}"
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
                if request.full_url in primary_urls:
                    attempts.append(RAW_HOST)
                    raise URLError("simulated primary transport outage")
                if request.full_url not in cdn_urls:
                    raise RuntimeError("Unexpected fallback URL")
                attempts.append(host)
                return real_open(request, timeout=timeout)

            opener.open = open_when_available
            return opener

        with patch.object(network, "build_opener", controlled_opener):
            encrypted = daily_feeds._download(published_url, LENGTH)

    if not attempts or attempts[0] != RAW_HOST or attempts[-1] not in CDN_HOSTS:
        raise RuntimeError("Expected a successful public fallback after blocking the primary host")
    digest = hashlib.sha256(encrypted).hexdigest()
    if len(encrypted) != LENGTH or digest != SHA256:
        raise RuntimeError("Encrypted public artifact differed from its publication receipt")
    return {"board": "hongguo", "source_date": "2026-11-11", "grant_repository": repository,
            "initial_cached_editions": 0,
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
    owner = official_ci_repository().split("/", 1)[0]
    feed_repository = next(repo for repo in FEED_REPOS if repo.split("/", 1)[0].lower() == owner.lower())
    receipt = check_transport(feed_repository)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print("Verified the public Hongguo ciphertext over", receipt["attempt_hosts"][-1])
