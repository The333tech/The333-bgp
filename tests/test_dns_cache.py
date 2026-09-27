import os
import socket
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main


class DnsCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = {"domains": {}}
        self.epoch = 1_700_000_000

    def resolve(self, seconds, answers=None, error=None):
        now = self.epoch + seconds
        results = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in (answers or [])]
        with (
            patch.object(main.time, "time", return_value=now),
            patch.object(main, "now_iso", return_value=datetime.fromtimestamp(now, timezone.utc).isoformat()),
            patch.object(main, "SERVICE_DNS_CACHE_GRACE_SECONDS", 60),
            patch.object(main, "SERVICE_DNS_RESOLVE_RETRIES", 1),
            patch.object(socket, "getaddrinfo", return_value=results, side_effect=error),
        ):
            networks, stat = main.dns_resolve_ipv4("example.com", self.cache)
        return {str(net) for net in networks}, stat

    def test_temporary_failure_keeps_fresh_answer_without_extending_lease(self):
        self.resolve(0, ["8.8.8.8"])
        error = socket.gaierror(socket.EAI_AGAIN, "temporary failure")
        for age in (1, 30, 60):
            routes, stat = self.resolve(age, error=error)
            self.assertEqual(routes, {"8.8.8.8/32"})
            self.assertTrue(stat["cache_hit"])
            self.assertEqual(stat["resolve_status"], "temporary_failure")
            self.assertEqual(stat["stale_ips"], ["8.8.8.8"])
        self.assertEqual(self.resolve(61, error=error)[0], set())

    def test_nxdomain_has_distinct_status_and_bounded_grace(self):
        self.resolve(0, ["8.8.8.8"])
        error = socket.gaierror(socket.EAI_NONAME, "not found")
        routes, stat = self.resolve(2, error=error)
        self.assertEqual(routes, {"8.8.8.8/32"})
        self.assertEqual(stat["resolve_status"], "not_found")
        self.assertEqual(self.resolve(61, error=error)[0], set())

    def test_failure_without_cache_does_not_invent_routes(self):
        routes, stat = self.resolve(0, error=socket.gaierror(socket.EAI_AGAIN, "temporary"))
        self.assertFalse(routes)
        self.assertFalse(stat["cache_hit"])

    def test_changed_answer_keeps_old_address_for_bounded_grace(self):
        self.resolve(0, ["8.8.8.8"])
        self.assertEqual(self.resolve(10, ["1.1.1.1"])[0], {"8.8.8.8/32", "1.1.1.1/32"})
        self.assertEqual(self.resolve(71, ["1.1.1.1"])[0], {"1.1.1.1/32"})

    def test_recovered_answer_clears_previous_stale_timestamp(self):
        self.resolve(0, ["8.8.8.8"])
        self.resolve(10, ["1.1.1.1"])
        self.resolve(50, ["8.8.8.8"])
        self.assertNotIn("stale_since", self.cache["domains"]["example.com"]["ips"]["8.8.8.8"])
        routes, _ = self.resolve(80, error=socket.gaierror(socket.EAI_AGAIN, "temporary"))
        self.assertIn("8.8.8.8/32", routes)
        self.assertEqual(self.resolve(111, error=socket.gaierror(socket.EAI_AGAIN, "temporary"))[0], set())

    def test_bad_cache_metadata_is_not_trusted(self):
        self.cache["domains"]["example.com"] = {"ips": {"8.8.8.8": {"last_seen": "invalid"}, "1.1.1.1": None}}
        self.assertEqual(self.resolve(0, error=socket.gaierror(socket.EAI_AGAIN, "temporary"))[0], set())

    def test_long_outage_is_not_extended_by_first_failed_check(self):
        self.resolve(0, ["8.8.8.8"])
        self.assertEqual(self.resolve(120, error=socket.gaierror(socket.EAI_AGAIN, "temporary"))[0], set())

    def test_private_answers_not_used_as_routes(self):
        routes, stat = self.resolve(0, ["192.168.1.1", "8.8.8.8"])
        self.assertEqual(routes, {"8.8.8.8/32"})
        self.assertEqual(stat["ignored"], 1)


if __name__ == "__main__":
    unittest.main()
