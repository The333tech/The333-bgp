FROM golang:1.26.5-alpine3.23@sha256:622e56dbc11a8cfe87cafa2331e9a201877271cbff918af53d3be315f3da88cc AS gobgp-builder

ARG GOBGP_VERSION=v4.7.0

RUN apk add --no-cache ca-certificates git \
    && go install github.com/osrg/gobgp/v4/cmd/gobgpd@${GOBGP_VERSION} \
    && go install github.com/osrg/gobgp/v4/cmd/gobgp@${GOBGP_VERSION}

FROM alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b

ARG GOBGP_CORE_IMAGE_VERSION=4.7.0-r4
ARG GOBGP_REVISION=v4.7.0

LABEL org.opencontainers.image.title="The333-BGP Core" \
      org.opencontainers.image.description="Hardened GoBGP routing core for The333-BGP" \
      org.opencontainers.image.source="https://github.com/The333tech/The333-bgp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${GOBGP_CORE_IMAGE_VERSION}" \
      org.opencontainers.image.revision="${GOBGP_REVISION}"

RUN apk add --no-cache ca-certificates tzdata \
    && update-ca-certificates

COPY --from=gobgp-builder /go/bin/gobgpd /usr/local/bin/gobgpd
COPY --from=gobgp-builder /go/bin/gobgp /usr/local/bin/gobgp
COPY docker/gobgp-entrypoint.sh /entrypoint.sh

RUN chmod 755 /entrypoint.sh

EXPOSE 1179/tcp 50051/tcp

ENTRYPOINT ["/entrypoint.sh"]
