ARG GO_IMAGE=golang:1.26.6-alpine3.24@sha256:af8d6740070b8906d12eae1c3e3ea0957fb63f492051ea05e354c38ef9fe88df
ARG ALPINE_IMAGE=alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b

FROM ${GO_IMAGE} AS builder

ARG TARGETARCH
ARG TARGETVARIANT
ARG AWG_GO_TAG=v0.2.19
ARG AWG_GO_REF=1cc94272ca8e9e223a5fe76382f5880f09d3c12d
ARG AWG_TOOLS_TAG=v1.0.20260618-2
ARG AWG_TOOLS_REF=61e741780e8465a67a7d7fb6cffe14a8a15d624a
ARG AWG_X_CRYPTO_VERSION=v0.53.0
ARG AWG_X_NET_VERSION=v0.56.0
ARG AWG_X_SYS_VERSION=v0.46.0

RUN apk add --no-cache \
      bash=5.3.9-r1 \
      build-base=0.5-r4 \
      git=2.54.0-r0 \
      linux-headers=7.0.0-r1 \
      make=4.4.1-r4

RUN git clone --depth 1 --branch "${AWG_GO_TAG}" \
      https://github.com/amnezia-vpn/amneziawg-go.git /src/amneziawg-go \
    && test "$(git -C /src/amneziawg-go rev-parse HEAD)" = "${AWG_GO_REF}" \
    && git clone --depth 1 --branch "${AWG_TOOLS_TAG}" \
      https://github.com/amnezia-vpn/amneziawg-tools.git /src/amneziawg-tools \
    && test "$(git -C /src/amneziawg-tools rev-parse HEAD)" = "${AWG_TOOLS_REF}" \
    && cd /src/amneziawg-go \
    && go get \
      "golang.org/x/crypto@${AWG_X_CRYPTO_VERSION}" \
      "golang.org/x/net@${AWG_X_NET_VERSION}" \
      "golang.org/x/sys@${AWG_X_SYS_VERSION}" \
    && go mod tidy \
    && go mod verify

RUN goarm="" \
    && if [ "${TARGETARCH}" = "arm" ]; then goarm="${TARGETVARIANT#v}"; fi \
    && cd /src/amneziawg-go \
    && CGO_ENABLED=0 GOOS=linux GOARCH="${TARGETARCH}" GOARM="${goarm}" make \
    && cd /src/amneziawg-tools/src \
    && make

FROM ${ALPINE_IMAGE}

ARG AWG_GO_TAG=v0.2.19
ARG AWG_GO_REF=1cc94272ca8e9e223a5fe76382f5880f09d3c12d
ARG AWG_TOOLS_TAG=v1.0.20260618-2
ARG AWG_TOOLS_REF=61e741780e8465a67a7d7fb6cffe14a8a15d624a
ARG AWG_X_CRYPTO_VERSION=v0.53.0
ARG AWG_X_NET_VERSION=v0.56.0
ARG AWG_X_SYS_VERSION=v0.46.0

LABEL org.opencontainers.image.title="The333 docker-awg candidate" \
      org.opencontainers.image.description="Reproducible AmneziaWG userspace gateway candidate for RouterOS containers" \
      org.opencontainers.image.source="https://github.com/The333tech/The333-bgp" \
      org.opencontainers.image.documentation="https://github.com/The333tech/The333-bgp/tree/main/extras/docker-awg" \
      org.opencontainers.image.licenses="GPL-2.0-only AND MIT" \
      org.opencontainers.image.version="${AWG_GO_TAG}+tools.${AWG_TOOLS_TAG}" \
      org.opencontainers.image.revision="awg-go:${AWG_GO_REF};awg-tools:${AWG_TOOLS_REF};x-crypto:${AWG_X_CRYPTO_VERSION};x-net:${AWG_X_NET_VERSION};x-sys:${AWG_X_SYS_VERSION}"

RUN apk add --no-cache \
      bash=5.3.9-r1 \
      ca-certificates=20260611-r0 \
      iproute2=7.0.0-r0 \
      iptables=1.8.13-r0 \
      iptables-legacy=1.8.13-r0 \
      openresolv=3.17.4-r0 \
    && mkdir -p /etc/amnezia/amneziawg /usr/share/licenses/amneziawg-go /usr/share/licenses/amneziawg-tools \
    && rm -f /usr/sbin/iptables /usr/sbin/ip6tables \
    && ln -s /usr/sbin/iptables-legacy /usr/sbin/iptables \
    && ln -s /usr/sbin/ip6tables-legacy /usr/sbin/ip6tables

COPY --from=builder /src/amneziawg-go/amneziawg-go /usr/bin/amneziawg-go
COPY --from=builder /src/amneziawg-tools/src/wg /usr/bin/awg
COPY --from=builder /src/amneziawg-tools/src/wg-quick/linux.bash /usr/bin/awg-quick
COPY --from=builder /src/amneziawg-go/LICENSE /usr/share/licenses/amneziawg-go/LICENSE
COPY --from=builder /src/amneziawg-tools/COPYING /usr/share/licenses/amneziawg-tools/COPYING
COPY entrypoint.sh /usr/local/sbin/docker-awg-entrypoint

RUN chmod 0755 \
      /usr/bin/amneziawg-go \
      /usr/bin/awg \
      /usr/bin/awg-quick \
      /usr/local/sbin/docker-awg-entrypoint

STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/sbin/docker-awg-entrypoint"]
