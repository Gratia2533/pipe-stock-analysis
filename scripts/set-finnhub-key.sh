#!/usr/bin/env bash

set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$script_dir/.." && pwd)
env_file="$repo_dir/.env"
env_template="$repo_dir/.env.example"

if [[ ! -t 0 ]]; then
  printf 'Error: run this script in an interactive terminal.\n' >&2
  exit 1
fi

printf 'Enter your Finnhub API key (input is hidden): '
IFS= read -r -s finnhub_api_key
printf '\n'

if [[ -z "$finnhub_api_key" ]]; then
  printf 'Error: the Finnhub API key cannot be empty.\n' >&2
  exit 1
fi

if [[ "$finnhub_api_key" == *$'\n'* || "$finnhub_api_key" == *$'\r'* ]]; then
  printf 'Error: the Finnhub API key cannot contain a newline.\n' >&2
  exit 1
fi

umask 077
temp_file=$(mktemp "$repo_dir/.env.tmp.XXXXXX")
cleanup() {
  unset finnhub_api_key
  if [[ -n "${temp_file:-}" && -f "$temp_file" ]]; then
    rm -f -- "$temp_file"
  fi
}
trap cleanup EXIT HUP INT TERM

source_file=""
if [[ -f "$env_file" ]]; then
  source_file="$env_file"
elif [[ -f "$env_template" ]]; then
  source_file="$env_template"
fi

key_written=false
if [[ -n "$source_file" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == FINNHUB_API_KEY=* ]]; then
      printf 'FINNHUB_API_KEY=%s\n' "$finnhub_api_key" >>"$temp_file"
      key_written=true
    else
      printf '%s\n' "$line" >>"$temp_file"
    fi
  done <"$source_file"
fi

if [[ "$key_written" == false ]]; then
  printf 'FINNHUB_API_KEY=%s\n' "$finnhub_api_key" >>"$temp_file"
fi

chmod 600 "$temp_file"
mv -f -- "$temp_file" "$env_file"
temp_file=""
unset finnhub_api_key

printf 'Finnhub API key saved to %s with mode 0600.\n' "$env_file"
printf 'The key was not printed and .env is excluded from Git.\n'

printf 'Restart the Finance MCP containers now? [y/N] '
IFS= read -r restart_answer
case "$restart_answer" in
  y | Y | yes | YES)
    compose_args=(
      --env-file "$env_file"
      -f "$repo_dir/compose.yaml"
    )
    if [[ -f "$repo_dir/compose.gratia.yaml" ]]; then
      compose_args+=(-f "$repo_dir/compose.gratia.yaml")
    fi
    docker compose "${compose_args[@]}" \
      up -d --force-recreate finance-internal finance-oauth

    internal_state=$(
      docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
        finance-mcp-internal |
        awk -F= '$1 == "FINNHUB_API_KEY" { if (length($2) > 0) print "set"; else print "empty" }'
    )
    oauth_state=$(
      docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
        finance-mcp-oauth |
        awk -F= '$1 == "FINNHUB_API_KEY" { if (length($2) > 0) print "set"; else print "empty" }'
    )

    if [[ "$internal_state" != set || "$oauth_state" != set ]]; then
      printf 'Error: containers restarted, but the key was not loaded correctly.\n' >&2
      exit 1
    fi
    printf 'Finance MCP containers restarted; FINNHUB_API_KEY is set in both.\n'
    ;;
  *)
    printf 'Saved without restarting. Re-run the script or recreate the containers later.\n'
    ;;
esac
