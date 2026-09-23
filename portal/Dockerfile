FROM node:24.21.0-alpine3.24@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm test && npm run build

FROM nginx:1.30.5-alpine@sha256:a5f2157a0302eb0c5e300415effb63a9e70ed1eb9c107283819bf6d149ab607c

ARG PRODUCT_VERSION=dev
ARG VCS_REF=unknown

LABEL org.opencontainers.image.title="The333-BGP Portal" \
      org.opencontainers.image.description="Web portal for operating and observing The333-BGP" \
      org.opencontainers.image.source="https://github.com/The333tech/The333-bgp" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${PRODUCT_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}"

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

RUN apk upgrade --no-cache \
    && sed -i '/^[[:space:]]*user[[:space:]]/d' /etc/nginx/nginx.conf

EXPOSE 8080 8443

ENTRYPOINT []
CMD ["nginx", "-g", "daemon off;"]
