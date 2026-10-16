import ipaddress
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("WEB_PASSWORD", "unit-test-password")

import app.main as main


class GeositeCompletenessTests(unittest.TestCase):
    def collect(self, text, *, depth=4, count=20, source="https://example.com/root"):
        with (
            patch.object(main, "SERVICE_GEOSITE_INCLUDE_MAX_DEPTH", depth),
            patch.object(main, "SERVICE_GEOSITE_MAX_INCLUDES_PER_PROVIDER", count),
            patch.object(main, "fetch_remote_text", return_value=("full:child.example\n", {"bytes": 20})) as fetch,
        ):
            result = main.collect_geosite_plain_domains(text, source, {})
        return result, fetch.call_count

    def test_depth_and_count_limits_report_skipped_includes(self):
        text = "full:base.example\ninclude:first\ninclude:second\n"
        for kwargs in ({"depth": 0}, {"count": 0}, {"count": 1}):
            with self.subTest(kwargs=kwargs):
                result, calls = self.collect(text, **kwargs)
                self.assertTrue(result["include_truncated"])
                self.assertEqual(result["skipped_include_count"], 2 - calls)
                self.assertIn("base.example", result["domains"])
        result, calls = self.collect(text)
        self.assertFalse(result["include_truncated"])
        self.assertEqual(result["skipped_include_count"], 0)
        self.assertEqual(calls, 2)

    def test_no_includes_is_complete_even_with_zero_budget(self):
        result, calls = self.collect("full:base.example\n", depth=0, count=0)
        self.assertFalse(result["include_truncated"])
        self.assertEqual(calls, 0)

    def test_local_include_without_remote_base_is_not_silently_complete(self):
        result, calls = self.collect("include:first\n", source="")
        self.assertTrue(result["include_truncated"])
        self.assertEqual(result["skipped_include_count"], 1)
        self.assertEqual(calls, 0)

    def test_visited_include_is_not_reported_as_missing(self):
        with (
            patch.object(main, "SERVICE_GEOSITE_INCLUDE_MAX_DEPTH", 1),
            patch.object(main, "SERVICE_GEOSITE_MAX_INCLUDES_PER_PROVIDER", 1),
            patch.object(main, "fetch_remote_text", return_value=("include:first\nfull:child.example", {"bytes": 20})) as fetch,
        ):
            result = main.collect_geosite_plain_domains("include:first\n", "https://example.com/root", {})
        self.assertFalse(result["include_truncated"])
        self.assertEqual(fetch.call_count, 1)

    def test_provider_keeps_selection_counts_and_source_age(self):
        source = {"source_url": "https://example.com/list", "provenance": {"fetched_at": "2026-10-01T00:00:00Z", "stale": True}}
        with (
            patch.object(main, "read_geosite_plain_text", return_value=("full:first.example\nfull:second.example\ninclude:extra\n", source)),
            patch.object(main, "SERVICE_GEOSITE_INCLUDE_MAX_DEPTH", 0),
            patch.object(main, "read_service_dns_cache", return_value={"domains": {}}),
            patch.object(main, "write_service_dns_cache"),
            patch.object(main, "SERVICE_DNS_RESOLVE_DELAY_SECONDS", 0),
            patch.object(main, "dns_resolve_ipv4", return_value=({ipaddress.ip_network("8.8.8.8/32")}, {"domain": "first.example"})) as dns,
        ):
            routes, stat = main.collect_geosite_plain_provider({"max_domains": 1})
        self.assertEqual(routes, {ipaddress.ip_network("8.8.8.8/32")})
        self.assertEqual(stat["parsed_domain_count"], 2)
        self.assertEqual(stat["selected_domain_count"], 1)
        self.assertTrue(stat["truncated"])
        self.assertTrue(stat["include_truncated"])
        self.assertEqual(stat["skipped_include_count"], 1)
        self.assertEqual(stat["source"]["provenance"]["fetched_at"], "2026-10-01T00:00:00Z")
        self.assertEqual(dns.call_count, 1)

    def test_include_read_later_at_shallower_depth_is_not_missing(self):
        with (
            patch.object(main, "SERVICE_GEOSITE_INCLUDE_MAX_DEPTH", 1),
            patch.object(main, "SERVICE_GEOSITE_MAX_INCLUDES_PER_PROVIDER", 10),
            patch.object(main, "fetch_remote_text", side_effect=[
                ("include:second\n", {"bytes": 15}),
                ("full:child.example\n", {"bytes": 20}),
            ]),
        ):
            result = main.collect_geosite_plain_domains(
                "include:first\ninclude:second\n", "https://example.com/root", {})
        self.assertFalse(result["include_truncated"])
        self.assertEqual(result["skipped_include_count"], 0)
        self.assertEqual(result["domains"], ["child.example"])
        result, _ = self.collect("full:base.example\ninclude:root\n", depth=0)
        self.assertFalse(result["include_truncated"])


if __name__ == "__main__":
    unittest.main()
