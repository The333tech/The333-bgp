import copy
import ipaddress
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

from app import dns_resolver
import app.main as main


class DnsWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.worker = Path(self.tmp.name) / "worker.py"

    def test_real_system_resolver_preserves_hosts_and_numeric_addresses(self):
        self.assertEqual(dns_resolver.resolve_ipv4("8.8.8.8", 3), ["8.8.8.8"])
        expected = sorted({item[4][0] for item in socket.getaddrinfo("localhost", None, socket.AF_INET, socket.SOCK_STREAM)})
        self.assertEqual(dns_resolver.resolve_ipv4("localhost", 3), expected)

    def test_real_hanging_workers_are_killed_reaped_and_capacity_recovers(self):
        self.worker.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        processes = []
        real_popen = subprocess.Popen

        def record_process(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        started = time.monotonic()
        with patch.object(dns_resolver, "_WORKER_PATH", self.worker), patch.object(subprocess, "Popen", side_effect=record_process):
            for _ in range(3):
                with self.assertRaises(TimeoutError):
                    dns_resolver.resolve_ipv4("example.com", 0.2)
        self.assertEqual(len(processes), 3)
        self.assertTrue(all(process.poll() is not None for process in processes))
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(dns_resolver.resolve_ipv4("8.8.8.8", 3), ["8.8.8.8"])

    def test_worker_failure_and_malformed_output_are_redacted(self):
        for source in ("raise RuntimeError('private-detail')", "print('invalid json')", "print('{\"addresses\":[\"::1\"]}')"):
            with self.subTest(source=source):
                self.worker.write_text(source, encoding="utf-8")
                with patch.object(dns_resolver, "_WORKER_PATH", self.worker):
                    with self.assertRaises(OSError) as error:
                        dns_resolver.resolve_ipv4("example.com", 3)
                self.assertNotIn("private-detail", str(error.exception))
        self.assertEqual(dns_resolver.resolve_ipv4("1.1.1.1", 3), ["1.1.1.1"])

    def test_worker_protocol_preserves_dns_error_code(self):
        self.worker.write_text(f"print('{{\"gaierror\":{socket.EAI_AGAIN}}}')", encoding="utf-8")
        with patch.object(dns_resolver, "_WORKER_PATH", self.worker):
            with self.assertRaises(socket.gaierror) as error:
                dns_resolver.resolve_ipv4("example.com", 3)
        self.assertEqual(error.exception.errno, socket.EAI_AGAIN)

    def test_capacity_wait_is_bounded_without_spawning_another_worker(self):
        semaphore = threading.BoundedSemaphore(1)
        semaphore.acquire()
        with patch.object(dns_resolver, "_WORKERS", semaphore), patch.object(subprocess, "run") as run:
            with self.assertRaises(TimeoutError):
                dns_resolver.resolve_ipv4("example.com", 0.02)
        run.assert_not_called()
        semaphore.release()

    def test_invalid_timeout_cannot_disable_deadlines(self):
        for value in ("0", "-1", "nan", "inf", "3601", "invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                dns_resolver.positive_seconds(value, "DNS timeout")


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class DnsBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.clock = Clock()
        self.stack.enter_context(patch.object(time, "monotonic", self.clock.monotonic))
        self.stack.enter_context(patch.object(time, "sleep", self.clock.sleep))
        for name, value in {
            "SERVICE_DNS_CACHE_FILE": self.root / "dns.json",
            "SERVICE_DNS_QUERY_TIMEOUT_SECONDS": 5,
            "SERVICE_DNS_PROVIDER_TIMEOUT_SECONDS": 60,
            "SERVICE_DNS_COLLECTION_TIMEOUT_SECONDS": 180,
            "SERVICE_DNS_RESOLVE_DELAY_SECONDS": 0,
        }.items():
            self.stack.enter_context(patch.object(main, name, value))

    def provider(self, *domains):
        return main.collect_service_provider({"type": "dns_domains", "name": "test", "domains": list(domains)})

    def slow_lookup(self, domain, timeout):
        duration = min(2, timeout)
        self.clock.value += duration
        if duration < 2:
            raise TimeoutError()
        return ["8.8.8.8"]

    def test_provider_budget_stops_starting_queries_and_marks_partial_data(self):
        with patch.object(main, "SERVICE_DNS_PROVIDER_TIMEOUT_SECONDS", 3), patch.object(dns_resolver, "resolve_ipv4", side_effect=self.slow_lookup) as resolve:
            routes, stat = self.provider("first.example", "second.example", "third.example")
        self.assertEqual(resolve.call_count, 2)
        self.assertEqual(self.clock.value, 3)
        self.assertTrue(stat["dns_budget_exceeded"])
        self.assertEqual(stat["dns_checked_domain_count"], 2)
        self.assertEqual(stat["dns_skipped_domain_count"], 1)
        self.assertIsNotNone(stat["error"])
        self.assertEqual(routes, {ipaddress.ip_network("8.8.8.8/32")})

    def test_nested_collection_budget_is_shared_and_reset_after_failure(self):
        with patch.object(main, "SERVICE_DNS_COLLECTION_TIMEOUT_SECONDS", 3), patch.object(dns_resolver, "resolve_ipv4", side_effect=self.slow_lookup) as resolve:
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                with main.service_dns_collection():
                    self.provider("first.example")
                    with main.service_dns_collection():
                        _, second = self.provider("second.example", "third.example")
                    _, third = self.provider("fourth.example")
                    raise RuntimeError("test failure")
            self.assertEqual(resolve.call_count, 2)
            self.assertTrue(second["dns_budget_exceeded"])
            self.assertTrue(third["dns_budget_exceeded"])
            _, fresh = self.provider("fresh.example")
        self.assertIsNone(fresh["error"])

    def test_retries_share_one_query_deadline_including_backoff(self):
        def fail(domain, timeout):
            self.clock.value += min(4.8, timeout)
            raise socket.gaierror(socket.EAI_AGAIN, "temporary")

        with patch.object(main, "SERVICE_DNS_RESOLVE_RETRY_DELAY_SECONDS", 0.5), patch.object(dns_resolver, "resolve_ipv4", side_effect=fail) as resolve:
            _, stat = self.provider("first.example")
        self.assertEqual(resolve.call_count, 1)
        self.assertEqual(self.clock.value, 5)
        self.assertEqual(stat["domain_stats"][0]["resolve_status"], "timeout")
        self.assertIsNotNone(stat["error"])

    def test_transient_failure_can_retry_and_recover_before_deadline(self):
        with patch.object(dns_resolver, "resolve_ipv4", side_effect=[socket.gaierror(socket.EAI_AGAIN, "temporary"), ["8.8.8.8"]]) as resolve:
            routes, stat = self.provider("first.example")
        self.assertEqual(resolve.call_count, 2)
        self.assertIsNone(stat["error"])
        self.assertTrue(routes)
        self.assertEqual(stat["domain_stats"][0]["resolve_attempts"], 2)

    def test_late_success_is_not_accepted_after_deadline(self):
        def late(domain, timeout):
            self.clock.value += timeout + 1
            return ["8.8.8.8"]

        with patch.object(dns_resolver, "resolve_ipv4", side_effect=late):
            routes, stat = self.provider("first.example")
        self.assertFalse(routes)
        self.assertIsNotNone(stat["error"])
        self.assertEqual(stat["domain_stats"][0]["resolve_status"], "timeout")

    def test_geosite_delay_consumes_provider_budget(self):
        with (
            patch.object(main, "read_geosite_plain_text", return_value=("full:first.example\nfull:second.example\n", {})),
            patch.object(main, "SERVICE_DNS_RESOLVE_DELAY_SECONDS", 0.2),
            patch.object(main, "SERVICE_DNS_PROVIDER_TIMEOUT_SECONDS", 0.3),
            patch.object(dns_resolver, "resolve_ipv4", side_effect=self.slow_lookup) as resolve,
        ):
            _, stat = main.collect_geosite_plain_provider({})
        self.assertEqual(resolve.call_count, 1)
        self.assertAlmostEqual(self.clock.value, 0.3)
        self.assertTrue(stat["dns_budget_exceeded"])
        self.assertEqual(stat["dns_skipped_domain_count"], 1)

    def test_timeout_with_valid_cache_is_degraded_not_falsely_fresh(self):
        with patch.object(dns_resolver, "resolve_ipv4", return_value=["8.8.8.8"]):
            self.provider("first.example")
        before = main.read_service_dns_cache()["domains"]["first.example"]["last_success_at"]
        with patch.object(dns_resolver, "resolve_ipv4", side_effect=TimeoutError()):
            routes, stat = self.provider("first.example")
        self.assertTrue(routes)
        self.assertIsNone(stat["error"])
        self.assertTrue(stat["domain_stats"][0]["cache_hit"])
        self.assertEqual(stat["domain_stats"][0]["last_success_at"], before)
        self.assertGreater(stat["warnings_count"], 0)

    def test_dns_deadline_does_not_interrupt_route_application(self):
        prefix = "8.8.8.8/32"

        def apply(*args, **kwargs):
            self.clock.value += 500
            return {"ok": True}

        with (
            patch.object(main, "publication_is_paused", return_value=False),
            patch.object(main, "read_route_exclusions_state", return_value={"active": [], "pending": None}),
            patch.object(main, "collect_static_prefixes", return_value=([prefix], {"source_stats": []})),
            patch.object(main, "collect_service_prefixes_for_update", return_value=([], {"enabled": False})),
            patch.object(main, "build_community_route_plan", return_value={"prefixes": [prefix], "route_communities": {prefix: []}}),
            patch.object(main, "read_last_good_snapshot", return_value=([prefix], {prefix: []})),
            patch.object(main, "validate_update_safety", return_value={}),
            patch.object(main, "apply_prefixes", side_effect=apply) as applied,
            patch.object(main, "write_last_good_snapshot") as saved,
            patch.object(main, "save_status"),
            patch.object(main, "append_update_history"),
        ):
            result = main.update_now()
        self.assertTrue(result["ok"])
        applied.assert_called_once()
        saved.assert_called_once_with([prefix], {prefix: []})

    def test_incomplete_dns_preserves_service_and_published_last_good(self):
        catalog = [{"id": "test", "providers": [
            {"type": "static_prefixes", "prefixes": ["8.8.8.8/32"]},
            {"type": "dns_domains", "domains": ["first.example"]},
        ]}]
        for name in ("SERVICE_CACHE_FILE", "SERVICE_ROUTES_FILE", "SERVICE_LAST_GOOD_ROUTES_FILE", "ADVERTISED_FILE", "LAST_GOOD_FILE", "LAST_GOOD_SNAPSHOT_FILE"):
            path = self.root / name
            self.stack.enter_context(patch.object(main, name, path))
        protected = (main.SERVICE_LAST_GOOD_ROUTES_FILE, main.ADVERTISED_FILE, main.LAST_GOOD_FILE, main.LAST_GOOD_SNAPSHOT_FILE)
        for path in protected:
            path.write_bytes(b"previous-working-snapshot\n")
        with (
            patch.object(main, "read_service_catalog", return_value=catalog),
            patch.object(main, "read_service_state", return_value={"services": {"test": {"enabled": True}}}),
            patch.object(main, "SERVICE_ROUTES_ENABLED", True),
            patch.object(main, "publication_is_paused", return_value=False),
            patch.object(main, "read_route_exclusions_state", return_value={"active": [], "pending": None}),
            patch.object(main, "collect_static_prefixes", return_value=(["8.8.8.8/32"], {"source_stats": []})),
            patch.object(dns_resolver, "resolve_ipv4", side_effect=TimeoutError()) as resolve,
            patch.object(main, "apply_prefixes") as apply,
            patch.object(main, "write_last_good_snapshot") as save,
        ):
            with self.assertRaisesRegex(RuntimeError, "route collection incomplete"):
                main.update_now()
            apply.assert_not_called()
            save.assert_not_called()
            for path in protected:
                self.assertEqual(path.read_bytes(), b"previous-working-snapshot\n")
            resolve.side_effect = None
            resolve.return_value = ["9.9.9.9"]
            result = main.build_service_routes()
        self.assertIsNone(result["service_stats"][0]["error"])
        self.assertEqual(main.read_lines(main.SERVICE_LAST_GOOD_ROUTES_FILE), ["8.8.8.8/32", "9.9.9.9/32"])


class DnsCacheConcurrencyTests(unittest.TestCase):
    def test_network_wait_does_not_hold_cache_lock_or_lose_parallel_domains(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main, "SERVICE_DNS_CACHE_FILE", Path(directory) / "dns.json"):
            entered, release = threading.Event(), threading.Event()

            def lookup(domain, timeout):
                if domain == "first.example":
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError()
                    return ["8.8.8.8"]
                return ["1.1.1.1"]

            with patch.object(dns_resolver, "resolve_ipv4", side_effect=lookup), ThreadPoolExecutor(max_workers=2) as pool:
                future = pool.submit(main.collect_service_provider, {"type": "dns_domains", "domains": ["first.example"]})
                try:
                    self.assertTrue(entered.wait(3))
                    acquired = main.SERVICE_DNS_CACHE_LOCK.acquire(timeout=0.2)
                    self.assertTrue(acquired)
                    if acquired:
                        main.SERVICE_DNS_CACHE_LOCK.release()
                    second = pool.submit(main.collect_service_provider, {"type": "dns_domains", "domains": ["second.example"]})
                    self.assertIsNone(second.result(timeout=2)[1]["error"])
                finally:
                    release.set()
                self.assertIsNone(future.result(timeout=3)[1]["error"])
            self.assertEqual(set(main.read_service_dns_cache()["domains"]), {"first.example", "second.example"})

    def test_older_observation_cannot_overwrite_newer_cache(self):
        cache = {"domains": {}}
        with patch.object(dns_resolver, "resolve_ipv4", return_value=["1.1.1.1"]):
            main.dns_resolve_ipv4("first.example", cache)
        before = copy.deepcopy(cache)
        old = {"domain": "first.example", "checked_at": "2000-01-01T00:00:00+00:00", "resolve_status": "resolved",
               "current_ips": ["8.8.8.8"], "ignored": 0, "error": None, "warning": None}
        routes, stat = main.apply_service_dns_lookup(cache, {"8.8.8.8"}, old)
        self.assertEqual(cache, before)
        self.assertEqual(routes, {ipaddress.ip_network("1.1.1.1/32")})
        self.assertEqual(stat["resolve_status"], "superseded")


if __name__ == "__main__":
    unittest.main()
