# API Key Authorization for civic.band Datasette Endpoints

## Problem

civic-observer can issue, expire, and revoke API keys, but the keys do not
work against the civic.band Datasette API. Corkboard's
`django_plugins/api_key_auth.py` still stubs the observer call, so production
rejects every `cb_live_` key. In addition, Anubis sits in front of corkboard
and challenges API clients before corkboard can validate their key.

## Goal

A request to a civic.band subdomain JSON endpoint carrying a valid
`cb_live_` key reaches Datasette without an Anubis challenge and without the
anonymous rate limit or result cap. Invalid, expired, or revoked keys receive
a JSON 401 from corkboard.

The API surface is the Datasette JSON endpoints under `*.civic.band`
(`.../meetings/...json`, `.../-/query.json`, etc.). The advertised but
unimplemented `civic.observer/api/v1/search/` endpoint is out of scope.

## Non-goals

- Per-key URL/domain scopes. The contract accepts a URL so scopes can be
  added later, but this change only validates key state and URL shape.
- Per-key rate limits. Valid keys bypass the anonymous IP limiter; a
  key-specific limiter can be added later.
- Changing how API keys are issued, revoked, or displayed.
- Granting Anubis bypass to `X-Service-Secret` backfill traffic.

## Architecture

```
Client -> Caddy -> Anubis -> corkboard ASGI wrapper -> Datasette
                                  |
                                  +-> POST civic.observer/api/v1/authorize
                                          (X-Service-Secret + full URL/key)
```

Anubis defers only requests that already look like civic.band API calls: a
`.json` path plus a `cb_live_` credential. Corkboard remains the authority on
whether the key is valid. All other traffic keeps the existing Anubis rules.

## civic-observer

### New endpoint

`POST /api/v1/authorize`, CSRF-exempt, plain Django `View`, JSON in/out.

Request:

```json
{
  "url": "https://alameda.ca.civic.band/meetings/agendas.json",
  "api_key": "cb_live_..."
}
```

Responses:

```json
{"allowed": true, "reason": "ok", "key_id": "...", "user_id": "...", "subdomain": "alameda.ca"}
{"allowed": false, "reason": "invalid_key"}
{"allowed": false, "reason": "invalid_url"}
```

- Malformed JSON or a missing `url`/`api_key` field: 400 `{"error": "Invalid request"}`.
- Missing, wrong, or unconfigured service secret: 401.
- Source IP outside the service allowlist: 403.
- Judgement failures return HTTP 200 with `allowed: false` so corkboard can
  cache the decision.

`POST /api/v1/validate-key` remains as a thin compatibility wrapper over the
same key-validation helper. It keeps its existing `{"valid": ...}` contract.

### URL validation

Accept `http` and `https` URLs that:

- have no username or password;
- have a hostname ending in `.<domain>` for a configured domain
  (`CIVIC_BAND_DOMAINS`, default `civic.band,adversely-star-koala.edgecompute.app`);
- have a non-empty subdomain label (root domain is rejected);
- have a path ending in `.json`;
- are at most 8192 characters.

The observer does not fetch the URL, so this is shape validation only, not
SSRF.

### Key validation

A key is allowed when it exists, is active, is unexpired, and (when it has an
owner) the owner is active. `last_used_at` is updated only for an allowed
judgement, using the existing five-minute throttle.

### Service authentication

The existing Tailscale-only IP check expands to a service-IP allowlist:
Tailscale CGNAT (`100.64.0.0/10`), loopback, and RFC1918 private ranges. The
`X-Service-Secret` check is unchanged. Public direct requests remain blocked.
When called through Caddy, corkboard's container address is the last
`X-Forwarded-For` hop, which is private, so the existing last-hop logic works.

## corkboard

### Observer call

`_call_civic_observer(api_key, url)` becomes a real `httpx.AsyncClient` POST to
`{CIVIC_OBSERVER_URL}/api/v1/authorize` with `X-Service-Secret`, a configurable
timeout (`CIVIC_OBSERVER_TIMEOUT`, default 5 seconds), and the full request URL.
It maps `allowed` to the wrapper's existing `valid` result and carries the
`reason` for logging.

- DEBUG-only `dev_` keys continue to work without an observer call.
- Transport errors, timeouts, and malformed responses fail closed but are not
  cached, so service recovery is immediate.
- Explicit denials are cached for `API_KEY_INVALID_TTL` (5 minutes); allows
  are cached for `API_KEY_VALID_TTL` (2 hours).

### Request URL construction

Build the judged URL from the forwarded scheme (falling back to the ASGI
scheme), the original `Host` header, the path, and the query string. Remove
the `api_key` parameter before sending it. Redact `api_key` from the
`corkboard.access` query-string field so query-string keys are not logged.

### Cache

The cache key includes the hashed key and a normalized URL
(`scheme://host/path`, query excluded because the current judgement does not
depend on it). This prevents a cached allow for one civic.band endpoint from
authorizing a different endpoint.

### Wrapper decision order

Within the existing `.json` branch:

1. First-party Referer, service secret, or research User-Agent: full access.
2. API key present: validate. Valid -> full access, skip rate limit and cap.
   Invalid -> 401.
3. No API key: existing per-IP rate limit, then cap results at 100.

The anonymous limiter therefore no longer runs before key validation.

## Anubis policy

Insert three `ALLOW` rules after the existing DENY rules and before the
GeoIP/WEIGH rules. Each matches `path_regex: '\.json$'` plus one credential:

- `headers_regex: {Authorization: '^Bearer cb_live_'}`
- `headers_regex: {X-API-Key: '^cb_live_'}`
- `expression` with a flat `all` list: `api_key` present and starting with
  `cb_live_`

The Anubis expression schema only supports flat lists of strings, so separate
rules are used instead of nested `any`/`all`. Because the rules come after
DENY rules, known-bad bot denials still win. Invalid `cb_live_` credentials
fall through to corkboard and receive a 401.

## Configuration

civic-observer:

- `CIVIC_BAND_DOMAINS` - comma-separated allowed API root domains.

corkboard:

- `CIVIC_OBSERVER_URL` - observer base URL. Production can use
  `https://civic.observer` (Caddy forwards to observer and the last XFF hop is
  private) or a private/Tailscale URL.
- `CIVIC_OBSERVER_SECRET` - must equal observer's
  `CORKBOARD_SERVICE_SECRET`.
- `CIVIC_OBSERVER_TIMEOUT` - HTTP timeout in seconds.

## Testing

civic-observer:

- valid key + valid URL -> allowed with key/user metadata;
- invalid, expired, revoked, inactive-owner key -> denied;
- bad scheme, credentials, root domain, non-JSON path, oversized URL -> denied;
- malformed/missing fields -> 400;
- service secret and IP allowlist behavior (private accepted, public rejected);
- `last_used_at` only updates on allowed judgements.

corkboard:

- observer HTTP success, explicit denial, 4xx/5xx, timeout, invalid JSON;
- no caching of transport failures; explicit denials cached;
- URL construction removes `api_key`; access-log redaction;
- cache key changes when the URL changes;
- wrapper: valid key bypasses rate limit/cap; invalid key -> 401; no key keeps
  limit/cap; trusted sources unaffected.

Manual Anubis verification:

- valid key returns JSON data with no challenge;
- invalid `cb_live_` key returns corkboard's 401 JSON, not a challenge;
- no-key and non-JSON requests keep the current challenge behavior.

## Rollout

1. Deploy civic-observer with `/api/v1/authorize`, URL validation, and the
   expanded IP allowlist.
2. Deploy corkboard with the real observer call and settings.
3. Deploy the Anubis policy change.

This order avoids a window where Anubis bypasses requests to a corkboard that
still stubs validation. Rollback is reverting the Anubis policy first, then
corkboard; the observer endpoint can remain deployed.

## Risks

- Observer availability is now on the API request path. Fail-closed behavior
  plus short invalid TTLs limits the blast radius, but an observer outage
  denies API traffic while cached allows last.
- API keys in query strings can be captured by upstream access logs. The
  Authorization header remains the preferred form; corkboard redacts its own
  access log.
- Observer-to-corkboard backfill requests use `X-Service-Secret`, not
  `cb_live_`, and are not covered by the new Anubis rules. If that path is
  challenged today, it needs a separate service-secret rule and a fail-closed
  corkboard check.
