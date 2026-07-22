FROM node:24.18.0-alpine3.23@sha256:595398b0081eacda8e1c4c5b97b76cd1020e4d58a8ebcb4843b9bca1e79e7436 AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm test && npm run build

FROM nginx:1.30.3-alpine3.23@sha256:0d3b80406a13a767339fbe2f41406d6c7da727ab89cf8fae399e81f780f814d1

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
