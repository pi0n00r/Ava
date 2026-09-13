#!/usr/bin/env bash
set -euo pipefail

echo "--> docker ps (prometheus/grafana)"
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}' | grep -E 'NAME|prometheus|grafana' || true

PROM_URL="${PROM_URL:-http://127.0.0.1:9090}"
HAVE_JQ=1
if ! command -v jq >/dev/null 2>&1; then
  HAVE_JQ=0
fi

echo "--> Prometheus targets"
curl -s "${PROM_URL}/api/v1/targets" | sed 's/,/\n/g' | grep -E 'scrapePool|health|lastScrape|endpoint' || true

now=$(date -u +%s)

echo "--> Gating active (sum)"
if [ "$HAVE_JQ" -eq 1 ]; then
  curl -s "${PROM_URL}/api/v1/query?query=sum(ai_agent_tts_gating_active)" | jq -r '.data.result[]?.value'
else
  curl -s "${PROM_URL}/api/v1/query?query=sum(ai_agent_tts_gating_active)"
fi

echo "--> Audio capture enabled (sum)"
if [ "$HAVE_JQ" -eq 1 ]; then
  curl -s "${PROM_URL}/api/v1/query?query=sum(ai_agent_audio_capture_enabled)" | jq -r '.data.result[]?.value'
else
  curl -s "${PROM_URL}/api/v1/query?query=sum(ai_agent_audio_capture_enabled)"
fi

# Optional streaming checks
if curl -s "${PROM_URL}/api/v1/label/__name__/values" | grep -q ai_agent_streaming_active; then
  echo "--> Streaming active (sum)"
  if [ "$HAVE_JQ" -eq 1 ]; then
    curl -s "${PROM_URL}/api/v1/query?query=sum(ai_agent_streaming_active)" | jq -r '.data.result[]?.value'
  else
    curl -s "${PROM_URL}/api/v1/query?query=sum(ai_agent_streaming_active)"
  fi
  echo "--> Streaming fallbacks (rate 5m)"
  if [ "$HAVE_JQ" -eq 1 ]; then
    curl -s "${PROM_URL}/api/v1/query?query=rate(ai_agent_streaming_fallbacks_total[5m])" | jq -r '.data.result[]?.value'
  else
    curl -s "${PROM_URL}/api/v1/query?query=rate(ai_agent_streaming_fallbacks_total[5m])"
  fi
fi
