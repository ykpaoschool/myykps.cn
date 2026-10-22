FROM nginx:1.27-alpine

ARG VERSION=0.1.1
LABEL org.opencontainers.image.title="myykps-portal" \
      org.opencontainers.image.description="go.myykps.cn 只读静态存档" \
      org.opencontainers.image.version="${VERSION}"

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY site/ /usr/share/nginx/html/

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
