# Contract: Local Ollama API

The pipeline talks to a local Ollama server over HTTP. Both the vision analyzer
and the text generator use the same `POST /api/generate` endpoint.

## Vision analysis

**Request**

```
POST {base_url}/api/generate
Content-Type: application/json
```

```json
{
  "model": "qwen2-vl",
  "prompt": "Describe this cat's expression, posture, and surroundings in detail.",
  "images": ["<base64-encoded image>"],
  "stream": false
}
```

- `images`: array of base64 data URIs without the `data:` prefix.
- `stream`: must be `false`.

**Response** (200)

```json
{
  "model": "qwen2-vl",
  "response": "The flat-faced cat stares ...",
  "done": true
}
```

**Failure contract**: connection error / timeout → wrapped in
`OllamaUnavailableError` with actionable guidance (`ollama serve`,
`ollama pull qwen2-vl`).

## Text generation

**Request**

```
POST {base_url}/api/generate
```

```json
{
  "model": "qwen2.5",
  "prompt": "<persona system prompt + visual context + JSON output instruction>",
  "stream": false
}
```

**Response** (200): `response` must contain a single JSON object:

```json
{
  "reel_text": "short hook <= 7 words",
  "caption": "caption <= 3 sentences",
  "hashtags": ["tag1", "tag2", "..."]
}
```

Parser tolerates markdown fences and recoverable JSON extraction; on failure
falls back to base hashtags rather than raising.

## Configuration

`base_url` defaults to `http://localhost:11434`; models and timeout live in
`config/config.yaml` under `ollama.*`.