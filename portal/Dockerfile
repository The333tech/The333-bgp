FROM node:24.22.1-alpine3.24@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm test && npm run build

FROM nginx:1.30.6-alpine@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951

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
