import express from "express";
import cors from "cors";
import dotenv from "dotenv";
import { CopilotRuntime } from "@copilotkit/runtime/v2";
import { createCopilotEndpointExpress } from "@copilotkit/runtime/v2/express";
import { LangGraphHttpAgent } from "@copilotkit/runtime/langgraph";

dotenv.config();

const app = express();

const allowedOrigins = (process.env.COPILOTKIT_CORS_ORIGINS ??
  "http://localhost:3000,http://127.0.0.1:3000")
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean);
app.use(cors({
  origin: (origin, callback) =>
    !origin || allowedOrigins.includes(origin)
      ? callback(null, true)
      : callback(new Error("Origin is not allowed")),
  credentials: true,
}));

const agentBase = process.env.AGENT_URL || "http://localhost:8010";

const base = agentBase.replace(/\/$/, "");

const runtime = new CopilotRuntime({
  agents: {
    // /session document co-editor — leave behavior unchanged
    predictive_state_updates: new LangGraphHttpAgent({
      url: agentBase,
    }),
    // Patient chart: auto-router (default UI agent)
    chart_router: new LangGraphHttpAgent({
      url: `${base}/router`,
    }),
    // Specialists (kept for debugging / direct use; router embeds collab+memory)
    dashboard_clinical: new LangGraphHttpAgent({
      url: `${base}/dashboard`,
    }),
    clinical_memory: new LangGraphHttpAgent({
      url: `${base}/memory`,
    }),
  },
});

// multi-route (default): mounts GET /threads, SSE, etc. Single-route only exposes POST /api/copilotkit
// and breaks the dev console / thread list (404 on /api/copilotkit/threads).
app.use(
  createCopilotEndpointExpress({
    runtime,
    basePath: "/api/copilotkit",
    mode: "multi-route",
    cors: false,
  }),
);

const port = Number(process.env.PORT ?? 4000);
const host = process.env.COPILOTKIT_BIND_HOST ?? "127.0.0.1";
app.listen(port, host, () => {
  console.log(`CopilotKit runtime listening at http://${host}:${port}/api/copilotkit`);
});
