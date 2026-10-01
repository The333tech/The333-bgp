import ipaddress
import hashlib
import json
from collections import defaultdict
from typing import Any


MAX_EXCLUSIONS = 64


def route_plan_sha256(route_communities: dict[str, list[str]]) -> str:
    entries = sorted(
        (str(ipaddress.ip_network(prefix, strict=True)), sorted(set(communities)))
        for prefix, communities in route_communities.items()
    )
    encoded = json.dumps(entries, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _coverage_intervals(prefixes: set[str]) -> list[tuple[int, int]]:
    networks = [ipaddress.ip_network(prefix, strict=True) for prefix in prefixes]
    if any(not isinstance(network, ipaddress.IPv4Network) for network in networks):
        raise ValueError("IPv6 route in publication plan")
    return [
        (int(network.network_address), int(network.broadcast_address))
        for network in ipaddress.collapse_addresses(networks)
    ]


def assess_route_change(
    current: dict[str, list[str]],
    candidate: dict[str, list[str]],
    max_delta_percent: float,
) -> dict[str, Any]:
    current_prefixes = set(current)
    candidate_prefixes = set(candidate)
    added = len(candidate_prefixes - current_prefixes)
    removed = len(current_prefixes - candidate_prefixes)
    changed_communities = sum(
        set(current[prefix]) != set(candidate[prefix])
        for prefix in current_prefixes & candidate_prefixes
    )

    old_intervals = _coverage_intervals(current_prefixes)
    new_intervals = _coverage_intervals(candidate_prefixes)
    old_coverage = sum(end - start + 1 for start, end in old_intervals)
    new_coverage = sum(end - start + 1 for start, end in new_intervals)
    intersection = 0
    old_index = new_index = 0
    while old_index < len(old_intervals) and new_index < len(new_intervals):
        old_start, old_end = old_intervals[old_index]
        new_start, new_end = new_intervals[new_index]
        intersection += max(0, min(old_end, new_end) - max(old_start, new_start) + 1)
        if old_end <= new_end:
            old_index += 1
        else:
            new_index += 1

    base = max(1, len(current_prefixes))
    address_base = max(1, old_coverage)
    churn_percent = round(100 * (added + removed + changed_communities) / base, 3)
    coverage_added_percent = round(100 * (new_coverage - intersection) / address_base, 3)
    coverage_removed_percent = round(100 * (old_coverage - intersection) / address_base, 3)
    reasons = []
    if current_prefixes and max_delta_percent > 0:
        if churn_percent > max_delta_percent:
            reasons.append("prefix_churn")
        if coverage_added_percent > max_delta_percent:
            reasons.append("coverage_added")
        if coverage_removed_percent > max_delta_percent:
            reasons.append("coverage_removed")

    return {
        "candidate_sha256": route_plan_sha256(candidate),
        "current_sha256": route_plan_sha256(current),
        "added_prefixes": added,
        "removed_prefixes": removed,
        "changed_communities": changed_communities,
        "prefix_churn_percent": churn_percent,
        "coverage_added_addresses": new_coverage - intersection,
        "coverage_removed_addresses": old_coverage - intersection,
        "coverage_added_percent": coverage_added_percent,
        "coverage_removed_percent": coverage_removed_percent,
        "requires_approval": bool(reasons),
        "reasons": reasons,
    }


def normalize_exclusions(values: list[str]) -> list[ipaddress.IPv4Network]:
    if not isinstance(values, list) or len(values) > MAX_EXCLUSIONS:
        raise ValueError(f"не более {MAX_EXCLUSIONS} исключений")
    networks: list[ipaddress.IPv4Network] = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or len(value) > 64:
            raise ValueError("исключение должно быть адресом IPv4 или CIDR")
        try:
            network = ipaddress.ip_network(value.strip(), strict=False)
        except ValueError as exc:
            raise ValueError("исключение должно быть адресом IPv4 или CIDR") from exc
        if not isinstance(network, ipaddress.IPv4Network) or network.prefixlen == 0:
            raise ValueError("IPv6 и 0.0.0.0/0 нельзя использовать как исключение")
        networks.append(network)
    return list(ipaddress.collapse_addresses(networks))


def subtract_exclusions(
    route_communities: dict[str, list[str]],
    exclusions: list[ipaddress.IPv4Network],
    max_routes: int,
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    if not exclusions:
        return dict(route_communities), {
            "before_count": len(route_communities),
            "after_count": len(route_communities),
            "fully_removed": 0,
            "split_routes": 0,
        }

    grouped: dict[tuple[str, ...], set[ipaddress.IPv4Network]] = defaultdict(set)
    fully_removed = 0
    split_routes = 0
    fragment_count = 0
    for prefix, communities in route_communities.items():
        network = ipaddress.ip_network(prefix, strict=True)
        if not isinstance(network, ipaddress.IPv4Network):
            raise ValueError("IPv6 route in publication plan")
        fragments = [network]
        for excluded in exclusions:
            if not network.overlaps(excluded):
                continue
            next_fragments: list[ipaddress.IPv4Network] = []
            for fragment in fragments:
                if fragment.subnet_of(excluded):
                    continue
                if excluded.subnet_of(fragment):
                    next_fragments.extend(fragment.address_exclude(excluded))
                else:
                    next_fragments.append(fragment)
            fragments = next_fragments
            if not fragments:
                break
        if not fragments:
            fully_removed += 1
        elif len(fragments) > 1:
            split_routes += 1
        grouped[tuple(sorted(set(communities)))].update(fragments)
        fragment_count += len(fragments)
        if fragment_count > max_routes * 4:
            raise ValueError("исключения создают слишком много фрагментов маршрутов")

    merged: dict[str, set[str]] = {}
    for community_set, networks in grouped.items():
        for network in ipaddress.collapse_addresses(sorted(networks)):
            merged.setdefault(str(network), set()).update(community_set)
            if len(merged) > max_routes:
                raise ValueError("после исключений превышен лимит маршрутов")
    result = {prefix: sorted(communities) for prefix, communities in merged.items()}
    return result, {
        "before_count": len(route_communities),
        "after_count": len(result),
        "fully_removed": fully_removed,
        "split_routes": split_routes,
    }
