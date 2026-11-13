# Registry API Contract (Phase 0)

Base prefix: `/api/registry`

Phase 0 is infrastructure only. Agent, Gateway, character loader and TTS do not
read these endpoints yet.

## Common conventions

- `resource_type`: `character` | `voice_profile` | `workflow`
- Mutating requests use Core's existing local auth middleware in production.
- `actor` in request bodies is ignored. Audit actor comes from the authenticated
  Core request context (`api-key`, `local-session`, `remote-api`).
- Concurrency:
  - update / publish require `expected_revision` or `If-Match`.
  - publish additionally requires `expected_published_revision` (nullable).
  - state changes accept optional `expected_revision` query.
- Idempotency: create supports `Idempotency-Key`.
- Request tracing: optional `X-Request-ID`; echoed in structured errors.
- Pagination: `limit` (1..100), `offset`.
- Errors:

```json
{
  "error": {
    "code": "revision_conflict",
    "message": "资源已被其他编辑更新",
    "details": {"expected": 2, "current": 3},
    "request_id": "..."
  }
}
```

## Endpoints

### Create

```http
POST /api/registry/{resource_type}
Idempotency-Key: optional
Content-Type: application/json

{
  "id": "bronya",
  "payload": {"name": "Bronya"},
  "change_note": "imported",
  "actor": "local"
}
```

Creates `revision=1`, `state=draft`, `published_revision=null`.

### List

```http
GET /api/registry/{resource_type}?status=active&limit=50&offset=0
```

### Get

```http
GET /api/registry/{resource_type}/{id}
```

Returns resource metadata plus `current` and `published` revision snapshots.

### Update

```http
PUT /api/registry/{resource_type}/{id}
If-Match: 2
Content-Type: application/json

{
  "payload": {"name": "Bronya v2"},
  "expected_revision": 2,
  "change_note": "edit"
}
```

Creates revision `2+1`; does not change published pointer.

### Publish

```http
POST /api/registry/{resource_type}/{id}/publish
Content-Type: application/json

{
  "revision": 3,
  "expected_current_revision": 3,
  "expected_published_revision": 2,
  "change_note": "tested"
}
```

`expected_published_revision` is required in body; initial publish uses `null`.

### Rollback

```http
POST /api/registry/{resource_type}/{id}/rollback
Content-Type: application/json

{
  "revision": 1,
  "expected_current_revision": 3,
  "expected_published_revision": 3
}
```

Publishes the historical revision; does not rewrite it.

### State changes

`expected_revision` is **required** for every state change:
missing -> `422 expected_revision_required`; mismatch -> `409 revision_conflict`.

```http
POST /api/registry/{resource_type}/{id}/disable?expected_revision=3
POST /api/registry/{resource_type}/{id}/enable?expected_revision=3
POST /api/registry/{resource_type}/{id}/archive?expected_revision=3
DELETE /api/registry/{resource_type}/{id}?expected_revision=3
```

Disable only from `active`; enable only from `disabled`; archive only from
`active|disabled`; delete is soft delete and rejects already-deleted resources.
Concurrent state changes: one winner, the other gets `409 invalid_status_transition`.

Delete is soft delete. Referenced resources return `409 reference_exists`.

### Revisions

```http
GET /api/registry/{resource_type}/{id}/revisions
```

### References

```http
POST /api/registry/{source_type}/{source_id}/references
{"target_type":"voice_profile","target_id":"bronya_voice"}

DELETE /api/registry/{source_type}/{source_id}/references?target_type=voice_profile&target_id=bronya_voice
```

## Read-only index (Phase 1)

These endpoints never write DB or legacy files. They project existing character
JSON, ComfyUI workflow JSON, `models.json` and character voice settings.

```text
GET /api/registry/index
GET /api/registry/index/characters
GET /api/registry/index/characters/{id}
GET /api/registry/index/voices
GET /api/registry/index/voices/{id}
GET /api/registry/index/workflows
GET /api/registry/index/workflows/{id}
GET /api/registry/index/image-models
```

`GET /api/registry/index` returns one aggregate payload:

```json
{
  "ok": true,
  "summary": {
    "characters": 13,
    "voices": 4,
    "workflows": 9,
    "image_models": 6,
    "read_only": true,
    "workflow_dir": "D:\\AI\\comic\\workflows"
  },
  "characters": [],
  "voices": [],
  "workflows": [],
  "image_models": []
}
```

Workflow items include `format` (`api_prompt` / `ui_workflow`), `node_count`,
`class_types`, `used_by`, `source` and `checksum`. No execution or import is
performed in Phase 1.

## Management (Phase 2A)

Draft-only import/validation endpoints. They create `draft` resources; they do
not publish and do not switch Runtime.

```text
POST /api/registry/manage/import/legacy
POST /api/registry/manage/characters/import   {"card": {...}, "id": "optional"}
POST /api/registry/manage/workflows/validate  {"payload": {...}, "live_check": true}
POST /api/registry/manage/workflows/import    {"payload": {...}, "id": "optional", "name": "optional"}
GET  /api/registry/manage/comfyui/nodes
GET  /api/registry/manage/runtime/status
POST /api/registry/manage/workflows/{id}/test
GET  /api/registry/manage/voice-assets
POST /api/registry/manage/voice-assets          multipart: file, kind, source, consent, reuse_policy
GET  /api/registry/manage/voice-assets/{id}
DELETE /api/registry/manage/voice-assets/{id}
```

`workflows/{id}/test` runs the current revision once through Media Gateway
(`POST /api/workflows/test`). It creates no TaskStore record and does not
publish or change the resource. The Gateway retains its normal `/api` auth.

`voice-assets` stores bytes under `data/voice_assets` (or
`registry.voice_assets_dir`), enforces allowed extensions and a 25MB limit,
records checksum/provenance/reuse policy, and soft-deletes metadata. It does not
delete the stored file.

`runtime/status` reports `off|shadow|enforce`; in `enforce` only resources with a
published revision are used by Agent, and unpublished resources fall back to
legacy files.

`live_check` fetches ComfyUI `/object_info`; if ComfyUI is down the validation
returns `dependencies.status = "unchecked"` instead of pretending nodes exist.
If a node class is missing, validation becomes `invalid`.

`workflows/validate` returns:

```json
{
  "ok": true,
  "validation": {
    "format": "api_prompt",
    "status": "valid",
    "errors": [],
    "warnings": [],
    "parameters": [
      {"nodeId": "2", "input": "steps", "expectedType": "number", "min": 1, "max": 64}
    ],
    "requirements": {"node_classes": ["KSampler", "SaveImage"], "high_risk_nodes": []},
    "node_count": 2,
    "checksum": "..."
  }
}
```

Only API Prompt JSON is accepted. UI Workflow JSON is rejected with an explicit
error. High-risk node classes are reported as warnings; execution is still not
available in Phase 2A.

## Runtime read contract

`RegistryService.resolve(type, id)` returns a `ResolvedResource` pointing at
`published_revision`. `RequestContext.resolve()` caches it for the request.

Not wired in Phase 0.
