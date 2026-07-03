import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx2 as httpx
from fastapi import HTTPException


os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main  # noqa: E402


class FakeNetworkStream:
    def __init__(self, address: str) -> None:
        self.address = address

    def get_extra_info(self, name: str) -> tuple[str, int] | None:
        if name == "server_addr":
            return self.address, 443
        return None


def response_with_peer(
    status_code: int,
    *,
    content: bytes = b"",
    headers: dict[str, str] | None = None,
    peer: str = "93.184.216.34",
) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=content,
        headers=headers,
        extensions={"network_stream": FakeNetworkStream(peer)},
    )


class RemoteUrlValidationTests(unittest.TestCase):
    def test_only_https_without_credentials_is_allowed(self) -> None:
        for unsafe in (
            "http://example.com/list.txt",
            "https://user:password@example.com/list.txt",
            "https://localhost/list.txt",
            "https://127.0.0.1/list.txt",
            "https://169.254.169.254/latest/meta-data",
        ):
            with self.subTest(url=unsafe), self.assertRaises(ValueError):
                main.validate_remote_url(unsafe)

    def test_hostname_resolving_to_private_address_is_rejected(self) -> None:
        with patch.object(
            main.socket,
            "getaddrinfo",
            return_value=[(2, 1, 6, "", ("192.168.1.10", 443))],
        ):
            with self.assertRaisesRegex(ValueError, "non-public"):
                main.validate_remote_url("https://source.example/list.txt")

    def test_public_ip_is_allowed(self) -> None:
        hostname, port, addresses = main.validate_remote_url("https://8.8.8.8/list.txt")

        self.assertEqual(hostname, "8.8.8.8")
        self.assertEqual(port, 443)
        self.assertEqual(addresses, {"8.8.8.8"})

    def test_connected_private_peer_is_rejected(self) -> None:
        response = response_with_peer(200, peer="10.0.0.1")

        with self.assertRaisesRegex(RuntimeError, "non-public"):
            main.validate_remote_response_peer(response)

    def test_remote_geoip_lines_use_common_route_policy(self) -> None:
        self.assertIsNone(main.normalize_geoip_prefix_line("0.0.0.0/0"))
        self.assertIsNone(main.normalize_geoip_prefix_line("192.168.1.0/24"))
        self.assertEqual(
            str(main.normalize_geoip_prefix_line("IP-CIDR,8.8.8.0/24")),
            "8.8.8.0/24",
        )

    def test_source_configuration_rejects_plain_http(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            main.validate_sources_config(
                [
                    {
                        "name": "unsafe-source",
                        "type": "url",
                        "enabled": False,
                        "url": "http://example.com/list.txt",
                    }
                ]
            )

        self.assertEqual(raised.exception.status_code, 400)


class RemoteFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self.temp_dir.name)
        self.real_client = httpx.Client

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def client_factory(self, handler: object):
        transport = httpx.MockTransport(handler)

        def factory(**kwargs: object) -> httpx.Client:
            return self.real_client(transport=transport, follow_redirects=False, trust_env=False)

        return factory

    def public_dns(self, *args: object, **kwargs: object) -> list[tuple[object, ...]]:
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    def test_response_size_limit_is_enforced(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return response_with_peer(200, content=b"x" * 32)

        with (
            patch.object(main, "REMOTE_FETCH_CACHE_DIR", self.cache_dir),
            patch.object(main.socket, "getaddrinfo", side_effect=self.public_dns),
            patch.object(main.httpx, "Client", side_effect=self.client_factory(handler)),
        ):
            with self.assertRaisesRegex(RuntimeError, "size limit"):
                main.fetch_remote_text(
                    "https://source.example/list.txt",
                    max_bytes=16,
                    use_cache=False,
                )

    def test_redirect_to_private_address_is_rejected_before_second_request(self) -> None:
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return response_with_peer(
                302,
                headers={"location": "https://127.0.0.1/internal"},
            )

        with (
            patch.object(main, "REMOTE_FETCH_CACHE_DIR", self.cache_dir),
            patch.object(main.socket, "getaddrinfo", side_effect=self.public_dns),
            patch.object(main.httpx, "Client", side_effect=self.client_factory(handler)),
        ):
            with self.assertRaises(ValueError):
                main.fetch_remote_text("https://source.example/list.txt", use_cache=False)

        self.assertEqual(requests, ["https://source.example/list.txt"])

    def test_verified_cache_is_used_after_transient_failure(self) -> None:
        payload = b"1.1.1.0/24\n"

        def success(request: httpx.Request) -> httpx.Response:
            return response_with_peer(200, content=payload, headers={"content-type": "text/plain"})

        def failure(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("simulated outage", request=request)

        common_patches = (
            patch.object(main, "REMOTE_FETCH_CACHE_DIR", self.cache_dir),
            patch.object(main.socket, "getaddrinfo", side_effect=self.public_dns),
        )

        with common_patches[0], common_patches[1], patch.object(
            main.httpx,
            "Client",
            side_effect=self.client_factory(success),
        ):
            text_value, fresh_meta = main.fetch_remote_text("https://source.example/list.txt")

        with (
            patch.object(main, "REMOTE_FETCH_CACHE_DIR", self.cache_dir),
            patch.object(main.socket, "getaddrinfo", side_effect=self.public_dns),
            patch.object(main.httpx, "Client", side_effect=self.client_factory(failure)),
        ):
            cached_text, cached_meta = main.fetch_remote_text("https://source.example/list.txt")

        self.assertEqual(text_value, payload.decode())
        self.assertEqual(cached_text, text_value)
        self.assertFalse(fresh_meta["cache_hit"])
        self.assertTrue(cached_meta["cache_hit"])
        self.assertTrue(cached_meta["stale"])

    def test_corrupted_cache_is_not_used(self) -> None:
        url = "https://source.example/list.txt"
        body_path, metadata_path = main.remote_fetch_cache_paths(url)

        with patch.object(main, "REMOTE_FETCH_CACHE_DIR", self.cache_dir):
            body_path, metadata_path = main.remote_fetch_cache_paths(url)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            body_path.write_bytes(b"tampered")
            metadata_path.write_text(
                '{"requested_url":"https://source.example/list.txt",'
                '"sha256":"invalid","fetched_at":"2026-07-03T00:00:00+00:00"}',
                encoding="utf-8",
            )
            cached = main.read_remote_fetch_cache(url, 1024)

        self.assertIsNone(cached)


if __name__ == "__main__":
    unittest.main()
