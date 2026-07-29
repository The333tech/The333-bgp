FROM golang:1.26.5-alpine3.24@sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2 AS gobgp-builder

ARG GOBGP_VERSION=v4.7.0
ARG GOBGP_TAG_REF=982fa664245fcd0dac3c8c408205bb2198b2cad3
ARG GOBGP_REF=8b5edc2c55cbec9e7df33123a07811a119d44542
ARG GOBGP_X_NET_VERSION=v0.56.0
ARG GOBGP_X_SYS_VERSION=v0.46.0
ARG GOBGP_X_TEXT_VERSION=v0.39.0
ARG GOBGP_GRPC_VERSION=v1.82.1

RUN apk add --no-cache ca-certificates git

COPY docker/build-gobgp.sh /usr/local/bin/build-gobgp
RUN /bin/sh /usr/local/bin/build-gobgp

FROM python:3.14.6-alpine3.24@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92

ARG PRODUCT_VERSION=dev
ARG VCS_REF=unknown
ARG GOBGP_VERSION=v4.7.0
ARG GOBGP_TAG_REF=982fa664245fcd0dac3c8c408205bb2198b2cad3
ARG GOBGP_REF=8b5edc2c55cbec9e7df33123a07811a119d44542
ARG GOBGP_X_NET_VERSION=v0.56.0
ARG GOBGP_X_SYS_VERSION=v0.46.0
ARG GOBGP_X_TEXT_VERSION=v0.39.0
ARG GOBGP_GRPC_VERSION=v1.82.1

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
