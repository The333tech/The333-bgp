#!/bin/sh
set -eu

: "${GOBGP_VERSION:?GOBGP_VERSION is required}"
: "${GOBGP_TAG_REF:?GOBGP_TAG_REF is required}"
: "${GOBGP_REF:?GOBGP_REF is required}"
: "${GOBGP_X_NET_VERSION:?GOBGP_X_NET_VERSION is required}"
: "${GOBGP_X_SYS_VERSION:?GOBGP_X_SYS_VERSION is required}"
: "${GOBGP_X_TEXT_VERSION:?GOBGP_X_TEXT_VERSION is required}"
: "${GOBGP_GRPC_VERSION:?GOBGP_GRPC_VERSION is required}"

git clone --depth 1 --branch "${GOBGP_VERSION}" \
  https://github.com/osrg/gobgp.git /src/gobgp
test "$(git -C /src/gobgp rev-parse "refs/tags/${GOBGP_VERSION}")" = "${GOBGP_TAG_REF}"
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

target_os="${TARGETOS:-$(go env GOOS)}"
target_arch="${TARGETARCH:-$(go env GOARCH)}"
target_variant="${TARGETVARIANT:-}"
case "${target_os}" in
  linux) ;;
  *) echo "unsupported GoBGP target OS: ${target_os}" >&2; exit 1 ;;
esac
case "${target_arch}" in
  amd64|arm64|arm|386|ppc64le|riscv64|s390x) ;;
  *) echo "unsupported GoBGP target architecture: ${target_arch}" >&2; exit 1 ;;
esac

goarm=""
if [ "${target_arch}" = "arm" ]; then
  goarm="${target_variant#v}"
  case "${goarm}" in
    5|6|7) ;;
    *) echo "unsupported GoBGP ARM variant: ${target_variant}" >&2; exit 1 ;;
  esac
fi

build_binary() {
  output="$1"
  package="$2"
  if [ -n "${goarm}" ]; then
    CGO_ENABLED=0 GOOS="${target_os}" GOARCH="${target_arch}" GOARM="${goarm}" \
      go build -mod=readonly -trimpath -buildvcs=false \
      -ldflags="-s -w -buildid=" -o "${output}" "${package}"
  else
    CGO_ENABLED=0 GOOS="${target_os}" GOARCH="${target_arch}" \
      go build -mod=readonly -trimpath -buildvcs=false \
      -ldflags="-s -w -buildid=" -o "${output}" "${package}"
  fi
}

mkdir -p /out
build_binary /out/gobgp ./cmd/gobgp
build_binary /out/gobgpd ./cmd/gobgpd
