FROM --platform=$BUILDPLATFORM golang:1.27.0-alpine3.24@sha256:4c9fe60190a2a3350ddc51de80d0224b8a6698d12bdfc999fee45ea9d6c46dbc AS gobgp-builder

ARG TARGETOS
ARG TARGETARCH
ARG TARGETVARIANT
ARG GOBGP_VERSION=v4.9.0
ARG GOBGP_TAG_REF=96ef11aedd066031f1bacb2c587baaa769ba07eb
ARG GOBGP_REF=01c5c4c27f9a1ac3b5927f433b5115f9b0eee791
ARG GOBGP_X_NET_VERSION=v0.59.0
ARG GOBGP_X_SYS_VERSION=v0.48.0
ARG GOBGP_X_TEXT_VERSION=v0.42.0
ARG GOBGP_GRPC_VERSION=v1.83.2

RUN apk add --no-cache ca-certificates git

COPY docker/build-gobgp.sh /usr/local/bin/build-gobgp
RUN /bin/sh /usr/local/bin/build-gobgp

FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6

ARG GOBGP_CORE_IMAGE_VERSION=4.9.0-r1
ARG GOBGP_VERSION=v4.9.0
ARG GOBGP_TAG_REF=96ef11aedd066031f1bacb2c587baaa769ba07eb
ARG GOBGP_REF=01c5c4c27f9a1ac3b5927f433b5115f9b0eee791
ARG GOBGP_X_NET_VERSION=v0.59.0
ARG GOBGP_X_SYS_VERSION=v0.48.0
ARG GOBGP_X_TEXT_VERSION=v0.42.0
ARG GOBGP_GRPC_VERSION=v1.83.2

LABEL org.opencontainers.image.title="The333-BGP Core" \
      org.opencontainers.image.description="Hardened GoBGP routing core for The333-BGP" \
      org.opencontainers.image.source="https://github.com/The333tech/The333-bgp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${GOBGP_CORE_IMAGE_VERSION}" \
      org.opencontainers.image.revision="${GOBGP_REF}" \
      org.opencontainers.image.the333.gobgp.version="${GOBGP_VERSION}" \
      org.opencontainers.image.the333.gobgp.tag-revision="${GOBGP_TAG_REF}" \
      org.opencontainers.image.the333.gobgp.modules="x-net:${GOBGP_X_NET_VERSION};x-sys:${GOBGP_X_SYS_VERSION};x-text:${GOBGP_X_TEXT_VERSION};grpc:${GOBGP_GRPC_VERSION}"

RUN apk add --no-cache ca-certificates tzdata \
    && update-ca-certificates

COPY --from=gobgp-builder /out/gobgpd /usr/local/bin/gobgpd
COPY --from=gobgp-builder /out/gobgp /usr/local/bin/gobgp
COPY docker/gobgp-entrypoint.sh /entrypoint.sh

RUN chmod 755 /entrypoint.sh

EXPOSE 1179/tcp 50051/tcp

ENTRYPOINT ["/entrypoint.sh"]
