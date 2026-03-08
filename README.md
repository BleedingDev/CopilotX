# 🚀 CopilotX

Local & Remote GitHub Copilot API proxy — use GPT-4o, Claude, Gemini and more via OpenAI/Anthropic compatible APIs.

Turn your GitHub Copilot subscription into an AI API server. Use **any model** available through Copilot with **any tool** that supports OpenAI or Anthropic SDKs — locally or on a remote VM.

## ✨ Features

- 🔐 **GitHub OAuth** — One-command login via Device Flow, or use existing token
- 🔄 **Auto Token Refresh** — Copilot JWT refreshed transparently before expiry
- 🔌 **Triple API Format** — OpenAI `/v1/chat/completions` + `/v1/responses` + Anthropic `/v1/messages`
- 🌊 **SSE Streaming** — Real-time streaming responses for all formats
- 👁️ **Vision Support** — Pass images through Responses API (auto-detected)
- 🎯 **Dynamic API URL** — Auto-discovers correct Copilot API endpoint per account type
- 📋 **Model Discovery** — Auto-fetch available models from Copilot
- ⚡ **Zero Config** — `pip install` → `auth login` → `serve` → done
- 🌐 **Remote Deploy** — Serve on `0.0.0.0` with API key protection, deploy behind Caddy for auto-HTTPS

## 🚀 Quick Start

### 1. Install

```bash
pip install copilotx
# or
uv pip install copilotx
```

### 2. Authenticate

```bash
# Option A: OAuth Device Flow (recommended)
copilotx auth login
# → Opens browser for GitHub authorization

# Option B: Use existing GitHub token
copilotx auth login --token ghp_xxxxx
# or
export GITHUB_TOKEN=ghp_xxxxx && copilotx auth login
```

### 3. Start Server

```bash
copilotx serve
```

Output:
```
🚀 CopilotX v3.0.0
✅ Copilot Token valid (28m remaining, auto-refresh)
� Local mode (localhost only)
🎯 API: api.enterprise.githubcopilot.com (auto-detected)
📋 Models: claude-opus-4.6, gpt-5-mini, gpt-5, gemini-2.5-pro, ...
📁 Port info: ~/.copilotx/server.json

🔗 OpenAI Chat:   http://127.0.0.1:24680/v1/chat/completions
🔗 Responses:     http://127.0.0.1:24680/v1/responses
🔗 Anthropic API: http://127.0.0.1:24680/v1/messages
🔗 Models:        http://127.0.0.1:24680/v1/models

Press Ctrl+C to stop
```

### 4. Use It

**Python (OpenAI SDK):**

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:24680/v1", api_key="copilotx")

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True,
)

for chunk in response:
    print(chunk.choices[0].delta.content or "", end="")
```

**Python (Anthropic SDK):**

```python
from anthropic import Anthropic

client = Anthropic(base_url="http://localhost:24680", api_key="copilotx")

message = client.messages.create(
    model="claude-sonnet-4",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}],
)
print(message.content[0].text)
```

**Claude Code:**

```bash
# Set environment variables
export ANTHROPIC_BASE_URL=http://localhost:24680
export ANTHROPIC_API_KEY=copilotx
claude
```

**Codex CLI (uses Responses API):**

```bash
export OPENAI_BASE_URL=http://localhost:24680/v1
export OPENAI_API_KEY=copilotx
codex
```

> Codex CLI uses the `/v1/responses` endpoint natively. CopilotX v2.1.0+ supports this
> including streaming, vision input, and `apply_patch` tool invocation.

**Python (OpenAI Responses API):**

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:24680/v1", api_key="copilotx")

response = client.responses.create(
    model="gpt-5-mini",
    input="Explain quicksort in 3 sentences.",
)
print(response.output_text)
```

**cURL:**

```bash
curl http://localhost:24680/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## 📡 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | OpenAI-compatible chat completions |
| `/v1/responses` | POST | OpenAI Responses API (streaming, vision, tools) |
| `/v1/messages` | POST | Anthropic-compatible messages |
| `/v1/models` | GET | List available models |
| `/health` | GET | Server health + token status |

## 🔧 CLI Commands

```bash
copilotx auth login              # OAuth Device Flow login
copilotx auth login --token XXX  # Quick login with existing token
copilotx auth status             # Show auth status
copilotx auth logout             # Clear credentials

copilotx models                  # List available models
copilotx serve                   # Start server (default: 127.0.0.1:24680)
copilotx serve --host 0.0.0.0   # Remote mode (bind all interfaces)
copilotx serve --port 9090       # Custom port (strict — fails if in use)

copilotx config claude-code      # Configure for local CopilotX
copilotx config claude-code -u https://...  # Configure for remote server

copilotx --version               # Show version
```

### Client Configuration

The `config` command auto-generates Claude Code configuration with smart defaults:

```bash
# Local mode — one command, zero prompts
copilotx config claude-code
# → Uses localhost:24680, auto-selects best models

# Remote mode — auto-reads API key from ~/.copilotx/.env
copilotx config claude-code -u https://api.polly.wang

# Custom models (optional)
copilotx config claude-code -m claude-opus-4.6 -s gpt-5-mini
```

Creates `~/.claude/settings.json` using `ANTHROPIC_AUTH_TOKEN` (bypasses Claude Code's API key format validation).

## 🏗️ How It Works

```
Your Tool (Claude Code / Codex / Python script)
    │
    │  OpenAI Chat / Responses / Anthropic format
    ▼
┌───────────────────────────────────┐
│  CopilotX (localhost:24680)       │
│                                   │
│  • /v1/chat/completions (pass)    │
│  • /v1/responses (pass + fix IDs) │
│  • /v1/messages (Anthropic→OpenAI)│
│  • Vision auto-detection          │
│  • apply_patch tool patching      │
│  • Token auto-refresh             │
└───────────────┬───────────────────┘
                │  OpenAI format
                ▼
  api.{individual|enterprise}.githubcopilot.com
  ├── /chat/completions
  └── /responses
  (GPT-5, Claude Opus 4.6, Gemini 2.5, ...)
```

CopilotX uses your GitHub Copilot subscription to access models. The correct API endpoint
is **auto-detected** from the Copilot token (`endpoints.api` field) — no hardcoded URLs.
OpenAI requests are **direct passthrough**, Anthropic requests are translated on-the-fly,
and Responses API streams get **ID synchronization** for consistent event tracking.

## 🔍 Port Discovery

When CopilotX starts, it writes `~/.copilotx/server.json`:

```json
{
  "host": "127.0.0.1",
  "port": 24680,
  "pid": 12345,
  "started_at": "2026-02-09T12:00:00+00:00",
  "base_url": "http://127.0.0.1:24680"
}
```

Other scripts can read this to discover the actual port:

```bash
# Bash/Zsh
PORT=$(python -c "import json; print(json.load(open('$HOME/.copilotx/server.json'))['port'])")
curl http://localhost:$PORT/health

# PowerShell
$info = Get-Content "$HOME\.copilotx\server.json" | ConvertFrom-Json
curl http://localhost:$($info.port)/health
```

The file is automatically cleaned up when the server stops.

## 🌐 Remote Deployment

Deploy CopilotX on a cloud VM to access your Copilot models from anywhere.

### Quick Setup (Azure VM / any Linux server)

```bash
# 1. Install
pip install copilotx

# 2. Authenticate
copilotx auth login

# 3. Set API key for remote protection
export COPILOTX_API_KEY=$(openssl rand -hex 32)
echo "Save this key: $COPILOTX_API_KEY"

# 4. Start in remote mode
copilotx serve --host 0.0.0.0
```

### Production Setup with Nginx + systemd

For production deployments with HTTPS, we recommend using Nginx as the reverse proxy.

**1. Install and configure systemd service:**

```bash
# Copy and customize the systemd service template
sudo cp deploy/copilotx.service /etc/systemd/system/

# Create environment file with your API key
mkdir -p ~/.copilotx
echo "COPILOTX_API_KEY=$(openssl rand -hex 32)" > ~/.copilotx/.env

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable --now copilotx
```

**2. Configure Nginx reverse proxy:**

```bash
# Copy the Nginx config template
sudo cp deploy/nginx-copilotx.conf /etc/nginx/sites-available/copilotx
sudo ln -s /etc/nginx/sites-available/copilotx /etc/nginx/sites-enabled/

# Get SSL certificate with Let's Encrypt
sudo certbot --nginx -d your-domain.com

# Reload Nginx
sudo nginx -t && sudo systemctl reload nginx
```

The `deploy/` directory includes ready-to-use templates:
- `copilotx.service` — systemd service unit (generic)
- `copilotx-azureuser.service` — systemd service unit (Azure VM with virtualenv)
- `nginx-copilotx.conf` — Nginx reverse proxy with SSL, rate limiting, and SSE support
- `nginx-copilotx-http.conf` — Temporary HTTP-only config for initial Let's Encrypt setup
- `Caddyfile` — Alternative Caddy config (simpler setup with auto-HTTPS)
- `.env.example` — Environment variables template

### Security Model

| Mode | Host | API Key | Behavior |
|------|------|---------|----------|
| **Local** | `127.0.0.1` (default) | Not needed | Fully open, localhost only |
| **Remote (protected)** | `0.0.0.0` | `COPILOTX_API_KEY` set | Localhost exempt, remote needs Bearer token |
| **Remote (open)** | `0.0.0.0` | Not set | ⚠️ Warning shown, fully open |

**Accessing from remote:**

```bash
# Use Bearer token
curl https://your-domain.com/v1/models \
  -H "Authorization: Bearer YOUR_API_KEY"

# Or x-api-key header
curl https://your-domain.com/v1/models \
  -H "x-api-key: YOUR_API_KEY"
```

**With OpenAI SDK:**

```python
client = OpenAI(
    base_url="https://your-domain.com/v1",
    api_key="YOUR_COPILOTX_API_KEY",
)
```

## 📋 Version Roadmap

| Version | Codename | Features |
|---------|----------|----------|
| v1.0.0 | Local | OAuth, dual API, streaming, model discovery |
| v2.0.0 | Remote | API key auth, remote deploy, Nginx/Caddy + systemd templates |
| v2.1.0 | Codex | Responses API, vision support, dynamic API URL, stream ID sync |
| v2.2.0 | Config | `copilotx config` command for client setup (Claude Code) |
| **v2.3.x** | **Polish** | **Error passthrough, stream error handling, test suite** |
| **v3.0.0** | **Multi-User** | **Token pool, user database, account rotation** |
| v3.1.0 | OpenRouter | OpenRouter mode |

## ⚠️ Disclaimer

This tool is for **personal local use only**. Please comply with
[GitHub Copilot Terms of Service](https://docs.github.com/en/copilot/overview-of-github-copilot/about-github-copilot-individual).
The author is not responsible for any account restrictions resulting from misuse.

## 📄 License

MIT
