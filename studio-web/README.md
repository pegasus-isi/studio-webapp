# PegasusAI Studio — Frontend

Next.js 15 web application for PegasusAI Studio.

## Development

### Prerequisites

- Node.js 20+
- Backend API running on port 8080 (see `../studio-api/README.md`)

### Setup

```bash
npm install
npm run dev
```

Open `http://localhost:3000`. API calls are proxied to `http://localhost:8080` via Next.js rewrites.

### Pages

| Route | Description |
|-------|-------------|
| `/dashboard` | Workflow list + detail with real-time status |
| `/workbench` | AI tool marketplace + terminal tabs (xterm.js) |
| `/chat` | SSE streaming chat with tool use |
| `/settings` | LLM provider configuration |

### Architecture

- **State**: Zustand for client state, TanStack Query for server state
- **Styling**: TailwindCSS with custom Pegasus color palette
- **Terminal**: xterm.js connected via WebSocket to backend PTY
- **Chat**: SSE streaming with tool call/result display
- **Build**: `next build` produces standalone output (~15MB)

### Build for production

```bash
npm run build
# Output in .next/standalone/
```
