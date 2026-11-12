#!/bin/sh
# Starts as root so a bind-mounted /data (created as root by Docker) can be handed to the app user,
# then drops privileges: the app itself never runs as root.
set -e
mkdir -p /data
if [ "$(stat -c %u /data)" != "$(id -u psacc)" ]; then
    chown -R psacc:psacc /data
fi
chmod 700 /data  # PSA tokens, OTP state, certificates and the password hash live here
mkdir -p /home/psacc
chown psacc:psacc /home/psacc
export HOME=/home/psacc  # setpriv keeps root's HOME otherwise
umask 077  # files the app creates are owner-only
cd /data  # setup code writes under PSACC_DATA_DIR; never leave /app (root-owned) as the CWD
exec setpriv --reuid=psacc --regid=psacc --init-groups -- "$@"
