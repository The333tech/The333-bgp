FROM node:24.18.0-alpine3.24@sha256:a0b9bf06e4e6193cf7a0f58816cc935ff8c2a908f81e6f1a95432d679c54fbfd AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm test && npm run build

FROM nginx:1.30.4-alpine@sha256:97d490c12ba55b4946b01546d1c3ed324e8d41ab1c9fcb2a616aa470620e5b46

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
