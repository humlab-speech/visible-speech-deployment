FROM docker.io/library/alpine:3.23

LABEL maintainer="Visible Speech <visp@umu.se>"
LABEL description="Tinyproxy sidecar for network-isolated VISP session containers"

# nftables works in rootless Podman user namespaces with CAP_NET_ADMIN.
# iptables (nft backend) and iptables-legacy both fail: nft needs CAP_NET_ADMIN
# in the root user namespace, and iptables-legacy can't find its xtables .so
# plugins inside the minimal Alpine image.
RUN apk add --no-cache tinyproxy socat nftables

COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# tinyproxy writes PID to /tmp
RUN mkdir -p /tmp && chmod 1777 /tmp

ENTRYPOINT ["/entrypoint.sh"]
