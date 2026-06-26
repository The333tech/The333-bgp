export type ReadyResponse = {
  ready: boolean;
  app: string;
  gobgp_ready: boolean;
  rib_count: number | null;
  advertised_count: number;
  last_good_count: number;
  status_ok: boolean;
  errors: string[];
  time: string;
};

export type DiagnosticsResponse = {
  ok: boolean;
  app: string;
  time: string;
  gobgp_ready: boolean;
  gobgp_rib_count: number | null;
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
  };
  time: string;
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
