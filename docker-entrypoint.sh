#!/bin/sh
# Starts as root so a bind-mounted /data (created as root by Docker) can be handed to the app user,
# then drops privileges: the app itself never runs as root.
set -e
mkdir -p /data
if [ "$(stat -c %u /data)" != "$(id -u psacc)" ]; then
    chown -R psacc:psacc /data
fi
exec setpriv --reuid=psacc --regid=psacc --init-groups -- "$@"
