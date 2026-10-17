# API Key Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `cb_live_` API keys work against civic.band Datasette JSON endpoints by validating them through civic-observer and deferring API-key decisions from Anubis to corkboard.

**Architecture:** civic-observer exposes `POST /api/v1/authorize` taking a URL and key, returning an `allowed` judgement. corkboard's subdomain ASGI wrapper calls it with the full request URL, caches the judgement, and enforces key validity before the anonymous rate limit. Anubis gains three ALLOW rules for `.json` requests carrying a `cb_live_` credential.

**Tech Stack:** Django 5, ASGI/DJP, httpx, Redis, pytest, pytest-asyncio, fakeredis, Anubis CEL policy.

**Spec:** `docs/superpowers/specs/2026-10-17-api-key-authorization-design.md`

## Global Constraints

- Keep `POST /api/v1/validate-key` backwards compatible: it still returns `{"valid": ...}`.
- The new endpoint is `POST /api/v1/authorize` and returns `{"allowed": ..., "reason": ...}`.
- Service auth is `X-Service-Secret` plus a source IP in Tailscale CGNAT, loopback, or RFC1918.
- URL shape: `http`/`https`, no credentials, subdomain under a configured root domain, path ending `.json`, max 8192 chars.
- Invalid keys and invalid URLs return HTTP 200 with `allowed: false`; malformed requests return 400.
- Corkboard fails closed on observer transport errors but does not cache them.
- Valid keys bypass the anonymous IP rate limit and result cap.
- Never log raw API keys.

## Review Focus

- Observer timeout or 500: API requests must be denied, not allowed, and the failure must not be cached for the full denial TTL.
- API key supplied in the query string: must be stripped from the URL sent to observer and redacted in corkboard's access log.
- Cached allow for one endpoint must not authorize a different endpoint or subdomain.
- Anubis rule ordering: a known-bad bot DENY rule must still win over the API-key ALLOW rules.
- Invalid `cb_live_` key: must reach corkboard and receive a JSON 401, not an Anubis challenge page.

---

### Task 1: Observer service-IP allowlist and setting

**Files:**
- Modify: `civic-observer/apikeys/internal_views.py`
- Modify: `civic-observer/config/settings/base.py`
- Test: `civic-observer/tests/apikeys/test_internal_views.py`

**Interfaces:**
- Produces: `is_service_ip(ip: str) -> bool`, `get_client_ip(request) -> str`, `_service_auth_error(request) -> JsonResponse | None`
- Produces setting: `CIVIC_BAND_DOMAINS: list[str]`

- [ ] **Step 1: Write failing tests**

Update `tests/apikeys/test_internal_views.py`:

```python
def test_rejects_public_ip(self, client, valid_secret):
    url = reverse("apikeys_internal:validate-key")
    with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
        response = client.post(
            url,
            data=json.dumps({"api_key": "cb_live_test"}),
            content_type="application/json",
            REMOTE_ADDR="8.8.8.8",
            HTTP_X_SERVICE_SECRET=valid_secret,
        )
    assert response.status_code == 403


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.17.0.1", "192.168.1.1"])
def test_accepts_private_service_ips(self, client, valid_secret, ip):
    _, raw_key = APIKey.create_key(name="Test Key")
    url = reverse("apikeys_internal:validate-key")
    with override_settings(CORKBOARD_SERVICE_SECRET=valid_secret):
        response = client.post(
            url,
            data=json.dumps({"api_key": raw_key}),
            content_type="application/json",
            REMOTE_ADDR=ip,
            HTTP_X_SERVICE_SECRET=valid_secret,
        )
    assert response.status_code == 200
```

Change the existing invalid-IP list so `192.168.1.1` and `10.0.0.1` are no longer expected to fail; keep only `100.63.255.255`, `100.128.0.0`, and `8.8.8.8`.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/apikeys/test_internal_views.py -q --no-cov`
Expected: private IPs return 403.

- [ ] **Step 3: Implement the allowlist**

In `apikeys/internal_views.py`:

```python
from ipaddress import ip_address, ip_network

SERVICE_NETWORKS = (
    ip_network("100.64.0.0/10"),  # Tailscale CGNAT
    ip_network("127.0.0.0/8"),
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("::1/128"),
    ip_network("fc00::/7"),
)


def is_service_ip(ip: str) -> bool:
    try:
        addr = ip_address(ip)
    except ValueError:
        return False
    return any(addr in network for network in SERVICE_NETWORKS)
```

Replace the `is_tailscale_ip` calls in `ValidateKeyView` with `is_service_ip`. Add to `config/settings/base.py`:

```python
CIVIC_BAND_DOMAINS = env.list(
    "CIVIC_BAND_DOMAINS",
    ["civic.band", "adversely-star-koala.edgecompute.app"],
)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/apikeys/test_internal_views.py -q --no-cov`
Expected: PASS.

---

### Task 2: Observer URL validation and `/authorize`

**Files:**
- Modify: `civic-observer/apikeys/internal_views.py`
- Modify: `civic-observer/apikeys/urls.py`
- Test: `civic-observer/tests/apikeys/test_internal_views.py`

**Interfaces:**
- Produces: `validate_api_url(url: str) -> tuple[bool, str | None]`
- Produces: `AuthorizeView`
- Produces URL name: `apikeys_internal:authorize`

- [ ] **Step 1: Write failing tests**

Add a `TestAuthorizeView` class:

```python
AUTHORIZE_URL = "https://alameda.ca.civic.band/meetings/agendas.json"


def _authorize(client, tailscale_ip, valid_secret, **payload):
    with override_settings(
        CORKBOARD_SERVICE_SECRET=valid_secret,
        CIVIC_BAND_DOMAINS=["civic.band"],
    ):
        return client.post(
            reverse("apikeys_internal:authorize"),
            data=json.dumps(payload),
            content_type="application/json",
            REMOTE_ADDR=tailscale_ip,
            HTTP_X_SERVICE_SECRET=valid_secret,
        )


def test_allows_valid_key_and_url(client, tailscale_ip, valid_secret):
    user = UserFactory()
    _, raw_key = APIKey.create_key(name="Test Key", user=user)
    response = _authorize(
        client, tailscale_ip, valid_secret, api_key=raw_key, url=AUTHORIZE_URL
    )
    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is True
    assert body["reason"] == "ok"
    assert body["subdomain"] == "alameda.ca"
    assert body["key_id"]
    assert body["user_id"] == str(user.id)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://alameda.ca.civic.band/meetings/agendas.json",
        "https://user:pass@alameda.ca.civic.band/meetings/agendas.json",
        "https://civic.band/meetings/agendas.json",
        "https://evil.example.com/meetings/agendas.json",
        "https://alameda.ca.civic.band/meetings/agendas",
        "https://alameda.ca.civic.band.evil.com/meetings/agendas.json",
    ],
)
def test_rejects_bad_urls(client, tailscale_ip, valid_secret, url):
    _, raw_key = APIKey.create_key(name="Test Key")
    response = _authorize(client, tailscale_ip, valid_secret, api_key=raw_key, url=url)
    assert response.status_code == 200
    assert response.json() == {"allowed": False, "reason": "invalid_url"}


def test_rejects_expired_key(client, tailscale_ip, valid_secret):
    _, raw_key = APIKey.create_key(
        name="Expired", expires_at=timezone.now() - timedelta(minutes=1)
    )
    response = _authorize(
        client, tailscale_ip, valid_secret, api_key=raw_key, url=AUTHORIZE_URL
    )
    assert response.json() == {"allowed": False, "reason": "invalid_key"}


def test_updates_last_used_only_when_allowed(client, tailscale_ip, valid_secret):
    key_obj, raw_key = APIKey.create_key(name="Test Key")
    _authorize(
        client,
        tailscale_ip,
        valid_secret,
        api_key=raw_key,
        url="https://bad.example/x.json",
    )
    key_obj.refresh_from_db()
    assert key_obj.last_used_at is None
    _authorize(client, tailscale_ip, valid_secret, api_key=raw_key, url=AUTHORIZE_URL)
    key_obj.refresh_from_db()
    assert key_obj.last_used_at is not None
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/apikeys/test_internal_views.py -q --no-cov`
Expected: `NoReverseMatch` for `authorize`.

- [ ] **Step 3: Implement URL validation and view**

In `apikeys/internal_views.py`:

```python
from urllib.parse import urlsplit

MAX_URL_LENGTH = 8192


def _configured_domains() -> list[str]:
    configured = getattr(settings, "CIVIC_BAND_DOMAINS", ["civic.band"])
    if isinstance(configured, str):
        return [d.strip().lower() for d in configured.split(",") if d.strip()]
    return [str(d).strip().lower() for d in configured if str(d).strip()]


def validate_api_url(url: str) -> tuple[bool, str | None]:
    if not url or len(url) > MAX_URL_LENGTH or any(c.isspace() for c in url):
        return False, None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False, None
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        return False, None
    hostname = (parsed.hostname or "").lower().rstrip(".")
    for domain in _configured_domains():
        suffix = f".{domain}"
        if hostname.endswith(suffix):
            subdomain = hostname[: -len(suffix)]
            if subdomain and parsed.path.endswith(".json"):
                return True, subdomain
    return False, None
```

Factor service auth and key lookup helpers:

```python
def _service_auth_error(request):
    if not is_service_ip(get_client_ip(request)):
        return JsonResponse({"error": "Forbidden"}, status=403)
    expected = getattr(settings, "CORKBOARD_SERVICE_SECRET", None)
    provided = request.headers.get("X-Service-Secret") or ""
    if not expected or not hmac.compare_digest(provided, expected):
        return JsonResponse({"error": "Unauthorized"}, status=401)
    return None


def _valid_key(api_key: str):
    if not api_key or not api_key.startswith("cb_live_"):
        return None
    try:
        key_obj = APIKey.objects.select_related("user").get(
            key_hash=APIKey.hash_key(api_key)
        )
    except APIKey.DoesNotExist:
        return None
    if not key_obj.is_valid():
        return None
    if key_obj.user and not key_obj.user.is_active:
        return None
    return key_obj


def _mark_used(key_obj) -> None:
    now = timezone.now()
    if (
        key_obj.last_used_at is None
        or now - key_obj.last_used_at >= LAST_USED_UPDATE_INTERVAL
    ):
        APIKey.objects.filter(pk=key_obj.pk).update(last_used_at=now)
```

Add `AuthorizeView` and refactor `ValidateKeyView` to use the helpers. Add to `apikeys/urls.py`:

```python
path("authorize", internal_views.AuthorizeView.as_view(), name="authorize"),
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/apikeys/test_internal_views.py -q --no-cov`
Expected: PASS.

---

### Task 3: Corkboard URL construction, redaction, cache, and observer call

**Files:**
- Modify: `corkboard/django_plugins/api_key_auth.py`
- Modify: `corkboard/config/settings.py`
- Test: `corkboard/tests/test_api_key_auth.py`

**Interfaces:**
- Produces: `build_request_url(scope: dict, host: str) -> str`
- Produces: `redact_query_string(query_string: bytes) -> str`
- Changes: `_cache_key(api_key: str, url: str = "") -> str`
- Changes: `validate_api_key(api_key: str, url: str) -> dict`
- Produces setting: `CIVIC_OBSERVER_TIMEOUT`

- [ ] **Step 1: Write failing tests**

```python
from django_plugins.api_key_auth import build_request_url, redact_query_string


def test_build_request_url_uses_forwarded_proto_and_strips_api_key():
    scope = {
        "scheme": "http",
        "path": "/meetings/agendas.json",
        "query_string": b"_size=100&api_key=cb_live_secret",
        "headers": [(b"x-forwarded-proto", b"https")],
    }
    assert build_request_url(scope, "alameda.ca.civic.band") == (
        "https://alameda.ca.civic.band/meetings/agendas.json?_size=100"
    )


def test_redact_query_string_hides_api_key():
    assert (
        redact_query_string(b"api_key=cb_live_secret&x=1")
        == "api_key=%5BREDACTED%5D&x=1"
    )
```

Add observer-call tests using a fake httpx client (accept a module-level factory or patch `httpx.AsyncClient`):

```python
async def test_observer_allowed_response(monkeypatch, settings):
    settings.CIVIC_OBSERVER_URL = "http://observer"
    settings.CIVIC_OBSERVER_SECRET = "secret"
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"allowed": True, "reason": "ok", "key_id": "k1"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None, headers=None):
            captured.update(url=url, json=json, headers=headers)
            return FakeResponse()

    monkeypatch.setattr("django_plugins.api_key_auth.httpx.AsyncClient", FakeClient)
    result = await _call_civic_observer("cb_live_x", "https://a.ca.civic.band/x.json")
    assert result["valid"] is True
    assert captured["url"] == "http://observer/api/v1/authorize"
    assert captured["json"]["url"] == "https://a.ca.civic.band/x.json"
    assert captured["headers"]["X-Service-Secret"] == "secret"
```

Add a timeout test that `_call_civic_observer` returns `None`, and `validate_api_key` returns `valid: False` without writing to fakeredis.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_api_key_auth.py -q`
Expected: import errors for the new helpers.

- [ ] **Step 3: Implement**

In `api_key_auth.py` add `httpx`, `parse_qsl`, `urlsplit`, `urlunsplit`, then:

```python
def build_request_url(scope: dict, host: str) -> str:
    scheme = ""
    for name, value in scope.get("headers", []):
        if name.decode("utf-8").lower() == "x-forwarded-proto":
            scheme = value.decode("utf-8").split(",")[0].strip()
            break
    scheme = scheme or scope.get("scheme", "http")
    params = parse_qsl(
        scope.get("query_string", b"").decode("utf-8", errors="replace"),
        keep_blank_values=True,
    )
    params = [(k, v) for k, v in params if k != "api_key"]
    return urlunsplit(
        (scheme, host, scope.get("path", "/") or "/", urlencode(params, doseq=True), "")
    )


def redact_query_string(query_string: bytes) -> str:
    params = parse_qsl(
        query_string.decode("utf-8", errors="replace"), keep_blank_values=True
    )
    return urlencode(
        [(k, "[REDACTED]" if k == "api_key" else v) for k, v in params], doseq=True
    )
```

Replace `_call_civic_observer` with the real `httpx.AsyncClient` call. Return `None` for non-200 responses, network errors, timeouts, and invalid JSON. `validate_api_key` must not cache `None`.

Add to `config/settings.py`:

```python
CIVIC_OBSERVER_TIMEOUT = float(get_env_variable("CIVIC_OBSERVER_TIMEOUT", "5"))
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_api_key_auth.py -q`
Expected: PASS.

---

### Task 4: Corkboard wrapper ordering and log redaction

**Files:**
- Modify: `corkboard/django_plugins/datasette_by_subdomain.py`
- Test: `corkboard/tests/test_datasette_by_subdomain.py`

**Interfaces:**
- Consumes: `build_request_url`, `redact_query_string`, `validate_api_key(api_key, url)`

- [ ] **Step 1: Write failing wrapper tests**

Add tests that mock the site lookup and Datasette, then assert:

- valid key -> 200, no `_size` cap, `check_rate_limit` not called;
- invalid key -> 401 and Datasette not called;
- no key -> rate limit/cap path still runs.

Use existing mocks from `test_datasette_by_subdomain.py` and patch `django_plugins.datasette_by_subdomain.validate_api_key`.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_datasette_by_subdomain.py -q`
Expected: new assertions fail (rate limit called first / stub rejects key).

- [ ] **Step 3: Implement**

In `_dispatch_datasette_request`, set:

```python
ctx["query_string"] = redact_query_string(scope.get("query_string", b""))
```

In `_run_datasette_request`, after `query_string` is available:

```python
request_url = build_request_url(scope, ctx["request_host"])
```

Replace the JSON auth block with:

```python
if not is_trusted_source:
    api_key = extract_api_key(scope["headers"], query_string)
    if api_key:
        result = await validate_api_key(api_key, request_url)
        if not result.get("valid"):
            ctx["api_key_reason"] = result.get("reason", "invalid_key")
            await send_401_response(send)
            return
        ctx["used_api_key"] = True
        ctx["api_key_reason"] = result.get("reason", "ok")
    else:
        client_ip = get_client_ip(scope)
        if await check_rate_limit(client_ip):
            ...
            await send_402_response(send, "rate_limit")
            return
        should_cap_results = True
```

Add `"api_key_reason": None` to the initial `ctx`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_api_key_auth.py tests/test_datasette_by_subdomain.py tests/test_logging.py -q`
Expected: PASS.

---

### Task 5: Anubis policy

**Files:**
- Modify: `corkboard/botPolicy.yaml`

- [ ] **Step 1: Add the rules**

After the `KeenableBot` DENY rule (line 47), before the GeoIP WEIGH rules:

```yaml
  # Defer API-key authorization to corkboard. Only JSON requests that carry
  # a CivicBand key skip Anubis; corkboard validates the key and returns 401
  # for invalid or expired keys. These rules come after the DENY rules so
  # known-bad bots are still denied first.
  - name: defer-api-key-authorization-header
    path_regex: '\.json$'
    headers_regex:
      Authorization: '^Bearer cb_live_'
    action: ALLOW

  - name: defer-api-key-x-header
    path_regex: '\.json$'
    headers_regex:
      X-API-Key: '^cb_live_'
    action: ALLOW

  - name: defer-api-key-query-param
    path_regex: '\.json$'
    action: ALLOW
    expression:
      all:
        - '"api_key" in query'
        - 'query["api_key"].startsWith("cb_live_")'
```

- [ ] **Step 2: Validate the policy**

Run: `docker run --rm -v "$PWD/botPolicy.yaml:/data/cfg/botPolicy.yaml:ro" ghcr.io/techarohq/anubis:latest anubis --help`
Expected: command starts; a full config check can be done by starting Anubis and checking logs for policy parse errors.

---

### Task 6: Config, docs, and verification

**Files:**
- Modify: `corkboard/.env.example`
- Modify: `civic-observer/.env.example`
- Optionally update `civic-observer/templates/api.html` example to a real civic.band JSON URL.

- [ ] **Step 1: Document settings**

corkboard `.env.example`:

```bash
# civic.observer API key authorization
CIVIC_OBSERVER_URL=https://civic.observer
CIVIC_OBSERVER_SECRET=dev-secret-change-me
CIVIC_OBSERVER_TIMEOUT=5
```

civic-observer `.env.example`:

```bash
# Root domains accepted by the internal /api/v1/authorize endpoint
CIVIC_BAND_DOMAINS=civic.band,adversely-star-koala.edgecompute.app
```

- [ ] **Step 2: Run targeted tests**

```bash
cd civic-observer && uv run pytest tests/apikeys/test_internal_views.py -q --no-cov
cd ../corkboard && uv run pytest tests/test_api_key_auth.py tests/test_datasette_by_subdomain.py tests/test_logging.py -q
```

- [ ] **Step 3: Run lint/format**

```bash
cd civic-observer && uv run --group dev ruff check apikeys config tests
cd ../corkboard && uv run ruff check django_plugins config tests
```

- [ ] **Step 4: Manual end-to-end check (deployment)**

1. Create a key in civic.observer.
2. `curl -H "Authorization: Bearer cb_live_..." https://<subdomain>.civic.band/meetings/agendas.json`
3. Confirm JSON data and no Anubis challenge.
4. Repeat with a bogus `cb_live_` key and confirm corkboard's JSON 401.
5. Request the same URL without a key and confirm Anubis still challenges.
