import ipaddress
import unittest

from app.route_policy import MAX_EXCLUSIONS, assess_route_change, normalize_exclusions, subtract_exclusions


class RoutePolicyTests(unittest.TestCase):
    def test_single_host_is_removed_without_suppressing_adjacent_addresses(self) -> None:
        excluded = normalize_exclusions(["1.1.1.42"])
        result, stats = subtract_exclusions({"1.1.1.0/24": []}, excluded, max_routes=30)

        self.assertEqual(stats["split_routes"], 1)
        self.assertEqual(len(result), 8)
        self.assertFalse(any(ipaddress.ip_address("1.1.1.42") in ipaddress.ip_network(prefix) for prefix in result))
        self.assertTrue(any(ipaddress.ip_address("1.1.1.43") in ipaddress.ip_network(prefix) for prefix in result))

    def test_exclusion_applies_to_base_and_overlapping_community_route(self) -> None:
        routes = {
            "1.1.0.0/16": [],
            "1.1.1.0/24": ["64500:510:1"],
        }
        result, _ = subtract_exclusions(routes, normalize_exclusions(["1.1.1.42/32"]), max_routes=100)

        self.assertFalse(any(ipaddress.ip_address("1.1.1.42") in ipaddress.ip_network(prefix) for prefix in result))
        self.assertTrue(any(communities == ["64500:510:1"] for communities in result.values()))
        self.assertTrue(any(ipaddress.ip_address("1.1.1.43") in ipaddress.ip_network(prefix) for prefix in result))

        reversed_result, _ = subtract_exclusions(
            dict(reversed(list(routes.items()))), normalize_exclusions(["1.1.1.42/32"]), max_routes=100,
        )
        self.assertEqual(reversed_result, result)
        self.assertEqual(reversed_result["1.1.1.0/27"], ["64500:510:1"])

    def test_supernet_exclusion_removes_contained_routes(self) -> None:
        result, stats = subtract_exclusions(
            {"1.1.1.0/24": ["64500:510:1"], "8.8.8.0/24": []},
            normalize_exclusions(["1.1.0.0/16"]), max_routes=10,
        )

        self.assertEqual(result, {"8.8.8.0/24": []})
        self.assertEqual(stats["fully_removed"], 1)

    def test_overlapping_exclusions_are_collapsed(self) -> None:
        self.assertEqual(normalize_exclusions(["1.1.1.0/25", "1.1.1.0/24", "1.1.1.0/25"]), [
            ipaddress.ip_network("1.1.1.0/24"),
        ])

    def test_route_growth_is_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "лимит маршрутов"):
            subtract_exclusions({"1.1.1.0/24": []}, normalize_exclusions(["1.1.1.42"]), max_routes=3)

    def test_invalid_and_overbroad_exclusions_are_rejected(self) -> None:
        for entries in (["0.0.0.0/0"], ["2001:db8::1"], ["not-an-ip"], [""], ["1.1.1.1"] * (MAX_EXCLUSIONS + 1)):
            with self.assertRaises(ValueError):
                normalize_exclusions(entries)

    def test_empty_exclusions_do_not_rewrite_the_plan(self) -> None:
        routes = {"1.1.0.0/16": [], "1.1.1.0/24": ["64500:510:1"]}

        result, stats = subtract_exclusions(routes, [], max_routes=10)

        self.assertEqual(result, routes)
        self.assertEqual(stats["after_count"], 2)

    def test_every_address_in_small_network_matches_exact_set_difference(self) -> None:
        parent = ipaddress.ip_network("1.1.1.0/29")
        candidates = [parent, *parent.subnets(new_prefix=30), *parent.subnets(new_prefix=32)]
        for first in candidates:
            for second in candidates:
                excluded = normalize_exclusions([str(first), str(second)])
                result, _ = subtract_exclusions({str(parent): []}, excluded, max_routes=30)
                for address in parent:
                    expected = address not in first and address not in second
                    actual = any(address in ipaddress.ip_network(prefix) for prefix in result)
                    self.assertEqual(actual, expected, (first, second, address))

    def test_guard_detects_same_count_but_large_coverage_swap(self) -> None:
        result = assess_route_change(
            {"1.1.1.0/24": []}, {"8.8.0.0/16": []}, max_delta_percent=35,
        )

        self.assertTrue(result["requires_approval"])
        self.assertEqual(result["coverage_removed_addresses"], 256)
        self.assertEqual(result["coverage_added_addresses"], 65536)
        self.assertIn("coverage_added", result["reasons"])

    def test_guard_detects_community_only_change(self) -> None:
        result = assess_route_change(
            {"1.1.1.0/24": ["64500:510:1"]},
            {"1.1.1.0/24": ["64500:510:2"]},
            max_delta_percent=35,
        )

        self.assertTrue(result["requires_approval"])
        self.assertEqual(result["changed_communities"], 1)
        self.assertEqual(result["coverage_added_addresses"], 0)
        self.assertEqual(result["coverage_removed_addresses"], 0)
        self.assertNotEqual(result["candidate_sha256"], result["current_sha256"])

    def test_guard_ignores_initial_install_and_small_stable_change(self) -> None:
        self.assertFalse(assess_route_change({}, {"1.1.1.0/24": []}, 35)["requires_approval"])
        old = {f"1.1.{index}.0/24": [] for index in range(10)}
        new = {**old, "8.8.8.0/24": []}
        self.assertFalse(assess_route_change(old, new, 35)["requires_approval"])


if __name__ == "__main__":
    unittest.main()
