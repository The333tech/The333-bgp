FROM --platform=$BUILDPLATFORM golang:1.27.2-alpine3.24@sha256:f92b6ef800e499660581efdabdf25d9d817a9d124eaf900924f0504e7e27e12d AS gobgp-builder

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

FROM python:3.14.8-alpine3.24@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72

ARG PRODUCT_VERSION=dev
ARG VCS_REF=unknown
ARG GOBGP_VERSION=v4.9.0
ARG GOBGP_TAG_REF=96ef11aedd066031f1bacb2c587baaa769ba07eb
ARG GOBGP_REF=01c5c4c27f9a1ac3b5927f433b5115f9b0eee791
ARG GOBGP_X_NET_VERSION=v0.59.0
ARG GOBGP_X_SYS_VERSION=v0.48.0
ARG GOBGP_X_TEXT_VERSION=v0.42.0
ARG GOBGP_GRPC_VERSION=v1.83.2

LABEL org.opencontainers.image.title="The333-BGP Backend" \
      org.opencontainers.image.description="Route calculation, source processing and control API for The333-BGP" \
      org.opencontainers.image.source="https://github.com/The333tech/The333-bgp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${PRODUCT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.the333.gobgp.version="${GOBGP_VERSION}" \
      org.opencontainers.image.the333.gobgp.tag-revision="${GOBGP_TAG_REF}" \
      org.opencontainers.image.the333.gobgp.revision="${GOBGP_REF}" \
      org.opencontainers.image.the333.gobgp.modules="x-net:${GOBGP_X_NET_VERSION};x-sys:${GOBGP_X_SYS_VERSION};x-text:${GOBGP_X_TEXT_VERSION};grpc:${GOBGP_GRPC_VERSION}"

RUN apk add --no-cache ca-certificates tzdata \
    && update-ca-certificates

WORKDIR /app

COPY --from=gobgp-builder /out/gobgp /usr/local/bin/gobgp
COPY requirements.txt /app/requirements.txt
RUN pip install --disable-pip-version-check --no-cache-dir --root-user-action=ignore --require-hashes -r /app/requirements.txt

COPY app /app/app
COPY VERSION /app/VERSION
COPY docker/backend-entrypoint.sh /entrypoint.sh

RUN chmod 755 /entrypoint.sh \
    && chmod -R a+rX /app

EXPOSE 8088/tcp

ENTRYPOINT ["/entrypoint.sh"]
