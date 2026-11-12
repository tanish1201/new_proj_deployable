#!/bin/sh
set -eu

dns_resolver="$(awk '$1 == "nameserver" { print $2; exit }' /etc/resolv.conf)"
if [ -z "$dns_resolver" ]; then
    echo "Could not determine the container DNS resolver" >&2
    exit 1
fi

export DNS_RESOLVER="$dns_resolver"
envsubst '${DNS_RESOLVER}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
