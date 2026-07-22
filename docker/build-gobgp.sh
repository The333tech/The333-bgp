#!/bin/sh
set -eu

: "${GOBGP_VERSION:?GOBGP_VERSION is required}"
: "${GOBGP_REF:?GOBGP_REF is required}"
: "${GOBGP_X_NET_VERSION:?GOBGP_X_NET_VERSION is required}"
: "${GOBGP_X_SYS_VERSION:?GOBGP_X_SYS_VERSION is required}"
: "${GOBGP_X_TEXT_VERSION:?GOBGP_X_TEXT_VERSION is required}"
: "${GOBGP_GRPC_VERSION:?GOBGP_GRPC_VERSION is required}"

git clone --depth 1 --branch "${GOBGP_VERSION}" \
  https://github.com/osrg/gobgp.git /src/gobgp
test "$(git -C /src/gobgp rev-parse HEAD)" = "${GOBGP_REF}"

cd /src/gobgp
go get \
  "golang.org/x/net@${GOBGP_X_NET_VERSION}" \
  "golang.org/x/sys@${GOBGP_X_SYS_VERSION}" \
  "golang.org/x/text@${GOBGP_X_TEXT_VERSION}" \
  "google.golang.org/grpc@${GOBGP_GRPC_VERSION}"
go mod tidy
go mod verify
go test ./cmd/gobgp ./cmd/gobgpd

mkdir -p /out
CGO_ENABLED=0 go build -mod=readonly -trimpath -buildvcs=false \
  -ldflags="-s -w -buildid=" -o /out/gobgp ./cmd/gobgp
CGO_ENABLED=0 go build -mod=readonly -trimpath -buildvcs=false \
  -ldflags="-s -w -buildid=" -o /out/gobgpd ./cmd/gobgpd
