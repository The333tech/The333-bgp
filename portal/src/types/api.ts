export type ReadyResponse = {
  ready: boolean;
  unconfigured?: boolean;
  app: string;
  gobgp_ready: boolean;
  rib_count: number | null;
  advertised_count: number;
  last_good_count: number;
  status_ok: boolean;
  publication?: PublicationStatus;
  errors: string[];
  time: string;
};

export type PublicationStatus = {
  mode: "publishing" | "pausing" | "paused" | "resuming" | "unconfirmed";
  confirmed: boolean;
  peer_admin_state: "up" | "down" | "unknown";
  peer_address: string;
  updated_at: string | null;
};

export type DiagnosticsResponse = {
  ok: boolean;
  app: string;
  time: string;
  gobgp_ready: boolean;
  publication?: PublicationStatus;
  gobgp_rib_count: number | null;
  route_freshness?: {
    status: "fresh" | "stale" | "paused" | "disabled" | "unknown";
    snapshot_updated_at: string | null;
    age_seconds: number | null;
    threshold_seconds: number;
  };
  sources_count: number | null;
  advertised_routes_summary?: {
    count: number;
    first_20: string[];
    last_20: string[];
  };
  last_good_routes_summary?: {
    count: number;
    first_20: string[];
    last_20: string[];
  };
  safe_env?: Record<string, unknown>;
  gobgp_neighbor?: string;
  gobgp_neighbor_detail?: string;
  gobgp_global?: string;
};

export type SourceItem = {
  name: string;
  enabled: boolean;
  type: "static" | "url";
  description?: string;
  group?: string;
  strategy?: string;
  priority?: number;
  url?: string;
  prefixes?: string[];
  manual_entries?: string[];
};

export type SourcesResponse = {
  ok: boolean;
  sources: SourceItem[];
  time: string;
};

export type UpdateHistoryRecord = {
  time: string;
  trigger: string;
  ok: boolean;
  mode: string;
  selected_source: string | null;
  selected_sources?: string[];
  selected_services?: string[];
  route_set_sha256?: string | null;
  final_count: number | null;
  advertised_count: number | null;
  added: number | null;
  deleted: number | null;
  unchanged: number | null;
  duration_seconds: number | null;
  error: string | null;
};

export type UpdateHistoryResponse = {
  ok: boolean;
  history: UpdateHistoryRecord[];
  count: number;
  file: string;
  time: string;
};

export type ServerResourcesResponse = {
  ok: boolean;
  cpu: {
    used_percent: number | null;
    cores: number | null;
  };
  ram: {
    total_bytes: number;
    available_bytes: number;
    used_bytes: number;
    used_percent: number | null;
  };
  disk: {
    path: string;
    total_bytes: number;
    used_bytes: number;
    free_bytes: number;
    used_percent: number | null;
    free_percent?: number | null;
    pressure?: "normal" | "warning" | "critical";
    update_ready?: boolean;
    update_min_free_bytes?: number;
  };
  runtime?: {
    ok: boolean;
    containers: Array<{
      key: "portal" | "backend" | "gobgp" | string;
      label: string;
      name: string;
      exists: boolean;
      status: string;
      health: string;
      started_at?: string | null;
      uptime_seconds?: number | null;
    }>;
    time?: string;
  };
  time: string;
};

export type RuntimeSettingsResponse = {
  ok: boolean;
  version: number;
  route_auto_update: {
    enabled: boolean;
    interval_minutes: number;
    interval_seconds: number;
    minimum_minutes: number;
    maximum_minutes: number;
  };
  automatic_backup: {
    enabled: boolean;
    interval_days: number;
    retention: number;
    mode: "on_change";
    last_checked_at?: string | null;
    last_result?: "created" | "unchanged" | "failed" | null;
    last_backup_name?: string | null;
    last_backup_at?: string | null;
    minimum_days: number;
    maximum_days: number;
    minimum_retention: number;
    maximum_retention: number;
  };
  updated_at?: string;
  time?: string;
};

export type RouteSetKind = "advertised" | "last_good" | "service" | "service_last_good";
export type RouteDiffSection = "added" | "removed" | "unchanged";

export type FileInfo = {
  name: string;
  path: string;
  size?: number;
  mtime?: string;
  exists: boolean;
};

export type RouteSetMeta = {
  kind: RouteSetKind;
  label: string;
  description: string;
  file: FileInfo;
};

export type RoutesResponse = {
  ok: boolean;
  kind: RouteSetKind;
  label: string;
  description: string;
  query: string;
  limit: number;
  offset: number;
  total_count: number;
  filtered_count: number;
  routes: string[];
  first_20: string[];
  last_20: string[];
  file: FileInfo;
  available_sets: RouteSetMeta[];
  time: string;
};

export type RouteLookupResponse = {
  ok: boolean;
  kind: "ip" | "domain";
  query: string;
  normalized: string;
  snapshot_updated_at: string | null;
  snapshot_route_count: number;
  checked_at: string;
  dns_error: string | null;
  dns_truncated: boolean;
  rib_checked?: boolean;
  rib_error?: string | null;
  publication_mode?: PublicationStatus["mode"];
  origin_available: boolean;
  addresses: Array<{
    address: string;
    probe_allowed?: boolean;
    match_count: number;
    matches: Array<{ prefix: string; communities: string[]; in_gobgp_rib?: boolean | null }>;
  }>;
};

export type RouteProbeResponse = {
  ok: boolean;
  address: string;
  tcp_443_connected: boolean;
  elapsed_ms: number;
  checked_at: string;
  checked_from: "backend_container";
};

export type RoutesDiffResponse = {
  ok: boolean;
  base: RouteSetMeta;
  target: RouteSetMeta;
  section: RouteDiffSection;
  section_label: string;
  query: string;
  limit: number;
  offset: number;
  counts: {
    base: number;
    target: number;
    added: number;
    removed: number;
    unchanged: number;
  };
  filtered_counts: {
    added: number;
    removed: number;
    unchanged: number;
  };
  routes: string[];
  available_sets: RouteSetMeta[];
  time: string;
};
