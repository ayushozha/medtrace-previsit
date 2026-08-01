import express from "express";
import cors from "cors";
import dotenv from "dotenv";
import { CopilotRuntime } from "@copilotkit/runtime/v2";
import { createCopilotEndpointExpress } from "@copilotkit/runtime/v2/express";
import { LangGraphHttpAgent } from "@copilotkit/runtime/langgraph";

dotenv.config();

const app = express();

app.use(cors({
  origin: "*",
  credentials: true,
}));

const runtime = new CopilotRuntime({
  agents: {
    predictive_state_updates: new LangGraphHttpAgent({
      url: process.env.AGENT_URL || "http://localhost:8010",
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
app.listen(port, () => {
  console.log(`CopilotKit runtime listening at http://localhost:${port}/api/copilotkit`);
});
