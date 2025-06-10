# Municipality Webhook API Documentation

## Overview

The MuniWebhookUpdateView provides a webhook endpoint for creating and updating municipality records. This endpoint accepts PUT or POST requests and can optionally be secured with a webhook secret.

## Endpoint

```
PUT/POST /munis/api/update/<subdomain>/
```

Where `<subdomain>` is the unique subdomain identifier for the municipality.

## Authentication

The endpoint supports optional authentication via webhook secret:

- If the `WEBHOOK_SECRET` environment variable is set, requests must include an Authorization header
- If no webhook secret is configured, the endpoint accepts all requests

### Authorization Header Format

The webhook secret can be provided in two formats:

```
Authorization: Bearer <your-webhook-secret>
```

or

```
Authorization: <your-webhook-secret>
```

## Request Format

### Headers

```
Content-Type: application/json
Authorization: Bearer <webhook-secret>  # Optional, see Authentication section
```

### Body

The request body must be valid JSON containing municipality data. The following fields are supported:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name | string | Yes | Name of the municipality |
| state | string | No | State abbreviation (e.g., "CA", "NY") |
| country | string | No | Country name (defaults to "USA") |
| kind | string | No | Type of municipality (e.g., "city", "county") |
| pages | integer | No | Number of pages (defaults to 0) |
| last_updated | date | No | Last update date in ISO format (YYYY-MM-DD) |
| latitude | float | No | Geographic latitude |
| longitude | float | No | Geographic longitude |
| popup_data | object | No | Additional JSON data for popups |

Note: The `subdomain` field is automatically set from the URL parameter and will override any value in the request body.

## Response Format

### Success Response

**Status Code:** 201 Created (new municipality) or 200 OK (updated municipality)

```json
{
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "subdomain": "example-city",
    "name": "Example City",
    "state": "CA",
    "country": "USA",
    "kind": "city",
    "pages": 10,
    "last_updated": "2024-01-15",
    "latitude": 37.7749,
    "longitude": -122.4194,
    "popup_data": null,
    "created": "2024-01-15T10:30:00Z",
    "modified": "2024-01-15T10:30:00Z",
    "action": "created"  // or "updated"
}
```

### Error Responses

**Invalid JSON (400 Bad Request):**
```json
{
    "error": "Invalid JSON"
}
```

**Missing Required Field (400 Bad Request):**
```json
{
    "error": "name field is required"
}
```

**Invalid Webhook Secret (401 Unauthorized):**
```json
{
    "error": "Invalid webhook secret"
}
```

**Other Errors (400 Bad Request):**
```json
{
    "error": "<error message>"
}
```

## Examples

### Create a New Municipality

```bash
curl -X POST http://localhost:8000/munis/api/update/san-francisco/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-webhook-secret" \
  -d '{
    "name": "San Francisco",
    "state": "CA",
    "kind": "city",
    "pages": 150,
    "last_updated": "2024-01-15",
    "latitude": 37.7749,
    "longitude": -122.4194,
    "popup_data": {
      "population": 873965,
      "area": "46.87 sq mi"
    }
  }'
```

### Update an Existing Municipality

```bash
curl -X PUT http://localhost:8000/munis/api/update/san-francisco/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-webhook-secret" \
  -d '{
    "pages": 175,
    "last_updated": "2024-01-20"
  }'
```

### Using Python with requests

```python
import requests
import json

url = "http://localhost:8000/munis/api/update/san-francisco/"
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer your-webhook-secret",
}
data = {
    "name": "San Francisco",
    "state": "CA",
    "kind": "city",
    "pages": 150,
    "last_updated": "2024-01-15",
    "latitude": 37.7749,
    "longitude": -122.4194,
}

# Create or update
response = requests.post(url, headers=headers, json=data)

if response.status_code in [200, 201]:
    result = response.json()
    print(f"Municipality {result['action']}: {result['name']}")
else:
    print(f"Error: {response.json()}")
```

## Notes

- The endpoint is CSRF-exempt to allow external webhook calls
- Both PUT and POST methods perform the same update_or_create operation
- If a municipality with the given subdomain exists, it will be updated; otherwise, a new one will be created
- All timestamps are returned in ISO 8601 format
- The `id` field in responses is a UUID string
