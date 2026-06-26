FROM golang:1.24-alpine AS gobgp-builder

ARG GOBGP_VERSION=v3.37.0

RUN apk add --no-cache git ca-certificates

RUN go install github.com/osrg/gobgp/v3/cmd/gobgpd@${GOBGP_VERSION} \
    && go install github.com/osrg/gobgp/v3/cmd/gobgp@${GOBGP_VERSION}

FROM python:3.14-alpine

RUN apk add --no-cache \
      bash \
      curl \
      ca-certificates \
      iproute2 \
      procps \
      tzdata \
    && update-ca-certificates

COPY --from=gobgp-builder /go/bin/gobgpd /usr/local/bin/gobgpd
COPY --from=gobgp-builder /go/bin/gobgp /usr/local/bin/gobgp


WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY VERSION /app/VERSION
COPY entrypoint.sh /entrypoint.sh

RUN chmod 755 /entrypoint.sh && chmod -R a+rX /app

EXPOSE 1179/tcp 8088/tcp 50051/tcp

ENTRYPOINT ["/entrypoint.sh"]
