FROM docker.io/library/alpine:3.20

LABEL maintainer="Visible Speech <visp@umu.se>"
LABEL description="Tinyproxy sidecar for network-isolated VISP session containers"

RUN apk add --no-cache tinyproxy socat

COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# tinyproxy writes PID to /tmp
RUN mkdir -p /tmp && chmod 1777 /tmp

ENTRYPOINT ["/entrypoint.sh"]
