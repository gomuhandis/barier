#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# Configures both Hikvision ANPR cameras so they push events to this backend.
#
# What this does (per ISAPI §9.1.3.2 + §5.x):
#   1) PUT  /ISAPI/Traffic/ANPR/alarmHttpPushProtocol  → baseLineProtocolEnabled=true
#   2) PUT  /ISAPI/Event/notification/httpHosts/1      → point to /isapi/anpr/<role>
#
# Credentials + camera IPs are read from the project .env next to this script.
# ------------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found. Copy .env.example to .env first."
    exit 1
fi

# load .env (ignores comments and empty lines)
set -a
# shellcheck disable=SC1090
. <(grep -vE '^\s*(#|$)' "$ENV_FILE")
set +a

: "${PUBLIC_LISTENER_URL:?PUBLIC_LISTENER_URL is required in .env}"

# parse host + port from PUBLIC_LISTENER_URL (e.g. http://192.168.1.50:8000)
LISTENER_NO_SCHEME="${PUBLIC_LISTENER_URL#http://}"
LISTENER_NO_SCHEME="${LISTENER_NO_SCHEME#https://}"
LISTENER_NO_SCHEME="${LISTENER_NO_SCHEME%/}"
LISTENER_HOST="${LISTENER_NO_SCHEME%%:*}"
LISTENER_PORT="${LISTENER_NO_SCHEME##*:}"
if [ "$LISTENER_HOST" = "$LISTENER_PORT" ]; then
    # no explicit port in URL
    if [[ "$PUBLIC_LISTENER_URL" == https://* ]]; then
        LISTENER_PORT=443
    else
        LISTENER_PORT=80
    fi
fi
LISTENER_PROTO="HTTP"
[[ "$PUBLIC_LISTENER_URL" == https://* ]] && LISTENER_PROTO="HTTPS"

echo "=============================================="
echo " Backend listener: $PUBLIC_LISTENER_URL"
echo " Parsed host:port: $LISTENER_HOST:$LISTENER_PORT ($LISTENER_PROTO)"
echo "=============================================="

# ------------------------------------------------------------------------------
# configure_one <cam_label> <role> <host> <port> <username> <password> <use_https>
# ------------------------------------------------------------------------------
configure_one() {
    local label="$1" role="$2" host="$3" port="$4" user="$5" pass="$6" https="$7"
    local scheme="http"
    [ "$https" = "true" ] && scheme="https"
    local cam_base="${scheme}://${host}:${port}"
    local listener_path="/isapi/anpr/${role}"
    local tls_flag=""
    [ "$https" = "true" ] && tls_flag="-k"

    echo
    echo "--- [$label] $cam_base  (role=$role) ---"

    # 1. enable baseline ANPR HTTP push
    echo "[1/2] Enable ANPR HTTP push (baseLineProtocolEnabled=true) ..."
    local body1='<?xml version="1.0" encoding="UTF-8"?>
<AlarmHttpPushProtocol version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">
    <baseLineProtocolEnabled>true</baseLineProtocolEnabled>
</AlarmHttpPushProtocol>'
    local code
    code=$(curl $tls_flag -sS --digest -u "$user:$pass" \
        -o /tmp/cam_cfg_resp.xml -w "%{http_code}" \
        -X PUT "$cam_base/ISAPI/Traffic/ANPR/alarmHttpPushProtocol" \
        -H "Content-Type: application/xml" --data "$body1" || echo "000")
    echo "      HTTP $code"
    [ "$code" != "200" ] && { echo "      body:"; sed 's/^/        /' /tmp/cam_cfg_resp.xml || true; }

    # 2. set httpHost #1 → <listener_host>:<listener_port><listener_path>
    echo "[2/2] Point httpHosts/1 → $PUBLIC_LISTENER_URL$listener_path ..."
    local body2="<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<HttpHostNotification version=\"2.0\" xmlns=\"http://www.isapi.org/ver20/XMLSchema\">
    <id>1</id>
    <url>${listener_path}</url>
    <protocolType>${LISTENER_PROTO}</protocolType>
    <parameterFormatType>XML</parameterFormatType>
    <addressingFormatType>ipaddress</addressingFormatType>
    <ipAddress>${LISTENER_HOST}</ipAddress>
    <portNo>${LISTENER_PORT}</portNo>
    <userName></userName>
    <httpAuthenticationMethod>none</httpAuthenticationMethod>
</HttpHostNotification>"
    code=$(curl $tls_flag -sS --digest -u "$user:$pass" \
        -o /tmp/cam_cfg_resp.xml -w "%{http_code}" \
        -X PUT "$cam_base/ISAPI/Event/notification/httpHosts/1" \
        -H "Content-Type: application/xml" --data "$body2" || echo "000")
    echo "      HTTP $code"
    [ "$code" != "200" ] && { echo "      body:"; sed 's/^/        /' /tmp/cam_cfg_resp.xml || true; }

    # bonus: barrier-gate sanity check (should return 200 + barrierGateStatus)
    echo "[check] GET barrierGateStatus ..."
    curl $tls_flag -sS --digest -u "$user:$pass" \
        "$cam_base/ISAPI/Parking/channels/1/barrierGate/barrierGateStatus" \
        | sed 's/^/        /' || true
    echo
}

configure_one \
    "${CAM1_NAME:-Camera1}" "${CAM1_ROLE:-entry}" \
    "${CAM1_HOST:?CAM1_HOST missing}" "${CAM1_PORT:-80}" \
    "${CAM1_USERNAME:?CAM1_USERNAME missing}" "${CAM1_PASSWORD:?CAM1_PASSWORD missing}" \
    "${CAM1_USE_HTTPS:-false}"

configure_one \
    "${CAM2_NAME:-Camera2}" "${CAM2_ROLE:-exit}" \
    "${CAM2_HOST:?CAM2_HOST missing}" "${CAM2_PORT:-80}" \
    "${CAM2_USERNAME:?CAM2_USERNAME missing}" "${CAM2_PASSWORD:?CAM2_PASSWORD missing}" \
    "${CAM2_USE_HTTPS:-false}"

rm -f /tmp/cam_cfg_resp.xml
echo
echo "Done. Cameras should now push ANPR events to:"
echo "  ${PUBLIC_LISTENER_URL}/isapi/anpr/entry"
echo "  ${PUBLIC_LISTENER_URL}/isapi/anpr/exit"