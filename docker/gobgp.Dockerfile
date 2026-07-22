FROM golang:1.26.5-alpine3.24@sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2 AS gobgp-builder

ARG GOBGP_VERSION=v4.7.0
ARG GOBGP_REF=982fa664245fcd0dac3c8c408205bb2198b2cad3
ARG GOBGP_X_NET_VERSION=v0.56.0
ARG GOBGP_X_SYS_VERSION=v0.46.0
ARG GOBGP_X_TEXT_VERSION=v0.39.0
ARG GOBGP_GRPC_VERSION=v1.82.1

RUN apk add --no-cache ca-certificates git

COPY docker/build-gobgp.sh /usr/local/bin/build-gobgp
RUN /bin/sh /usr/local/bin/build-gobgp

FROM alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b

ARG GOBGP_CORE_IMAGE_VERSION=4.7.0-r5
ARG GOBGP_VERSION=v4.7.0
ARG GOBGP_REF=982fa664245fcd0dac3c8c408205bb2198b2cad3
ARG GOBGP_X_NET_VERSION=v0.56.0
ARG GOBGP_X_SYS_VERSION=v0.46.0
ARG GOBGP_X_TEXT_VERSION=v0.39.0
ARG GOBGP_GRPC_VERSION=v1.82.1

LABEL org.opencontainers.image.title="The333-BGP Core" \
      org.opencontainers.image.description="Hardened GoBGP routing core for The333-BGP" \
      org.opencontainers.image.source="https://github.com/The333tech/The333-bgp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${GOBGP_CORE_IMAGE_VERSION}" \
      org.opencontainers.image.revision="${GOBGP_REF}" \
      org.opencontainers.image.the333.gobgp.version="${GOBGP_VERSION}" \
      org.opencontainers.image.the333.gobgp.modules="x-net:${GOBGP_X_NET_VERSION};x-sys:${GOBGP_X_SYS_VERSION};x-text:${GOBGP_X_TEXT_VERSION};grpc:${GOBGP_GRPC_VERSION}"

RUN apk add --no-cache ca-certificates tzdata \
    && update-ca-certificates

COPY --from=gobgp-builder /out/gobgpd /usr/local/bin/gobgpd
COPY --from=gobgp-builder /out/gobgp /usr/local/bin/gobgp
COPY docker/gobgp-entrypoint.sh /entrypoint.sh

RUN chmod 755 /entrypoint.sh

EXPOSE 1179/tcp 50051/tcp

ENTRYPOINT ["/entrypoint.sh"]
