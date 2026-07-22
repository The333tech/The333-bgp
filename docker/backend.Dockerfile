FROM golang:1.26.5-alpine3.24@sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2 AS gobgp-builder

ARG GOBGP_VERSION=v4.7.0

RUN apk add --no-cache ca-certificates git \
    && go install github.com/osrg/gobgp/v4/cmd/gobgpd@${GOBGP_VERSION} \
    && go install github.com/osrg/gobgp/v4/cmd/gobgp@${GOBGP_VERSION}

FROM python:3.14.6-alpine3.24@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92

ARG PRODUCT_VERSION=dev
ARG VCS_REF=unknown

LABEL org.opencontainers.image.title="The333-BGP Backend" \
      org.opencontainers.image.description="Route calculation, source processing and control API for The333-BGP" \
      org.opencontainers.image.source="https://github.com/The333tech/The333-bgp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${PRODUCT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

RUN apk add --no-cache ca-certificates tzdata \
    && update-ca-certificates

WORKDIR /app

COPY --from=gobgp-builder /go/bin/gobgp /usr/local/bin/gobgp
COPY requirements.txt /app/requirements.txt
RUN pip install --disable-pip-version-check --no-cache-dir --root-user-action=ignore --require-hashes -r /app/requirements.txt

COPY app /app/app
COPY VERSION /app/VERSION
COPY docker/backend-entrypoint.sh /entrypoint.sh

RUN chmod 755 /entrypoint.sh \
    && chmod -R a+rX /app

EXPOSE 8088/tcp

ENTRYPOINT ["/entrypoint.sh"]
