#!/usr/bin/env python3
"""Single place naming this CosmOS install's online-services server (see
theskysanctum/CosmOS-online) - every other module in this package imports
these constants rather than hardcoding a URL of its own.

BASE_DOMAIN is the real apex (theskysanctum.com), not a subdomain -
Cloudflare's free Universal SSL wildcard only covers one DNS label deep,
so a two-label prefix like auth.online.${BASE_DOMAIN} gets no
certificate at all and fails TLS (confirmed live; see CosmOS's own
OPERATIONS.md and the online-services deploy repo for the same fix).
Each online service instead gets a single hyphenated label
(online-auth, online-api, ...) - but no server has actually been
provisioned/deployed there yet as of this writing (see CosmOS-online's
README "Status" section)."""
BASE_DOMAIN = "theskysanctum.com"

AUTH_BASE = f"https://online-auth.{BASE_DOMAIN}"
API_BASE = f"https://online-api.{BASE_DOMAIN}"

KEYCLOAK_REALM = "cosmoplex"
# A public (no-secret) Keycloak client with the OAuth2 Device
# Authorization Grant enabled - one-time manual setup in the Keycloak
# admin console, same category of prerequisite as CosmOS-online's own
# OPERATIONS.md steps (creating OAuth clients isn't a secret-rotation
# task, so no script here creates it).
KEYCLOAK_CLIENT_ID = "cosmos-client"
