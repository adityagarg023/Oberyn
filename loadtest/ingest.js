import http from "k6/http";
import { check } from "k6";
import { Counter } from "k6/metrics";

const statuses = new Counter("ingest_http_status_total");
const baseUrl = (__ENV.OBERYN_INGEST_URL || "http://localhost:8000").replace(/\/$/, "");
const projectId = __ENV.OBERYN_PROJECT_ID;
const projectKey = __ENV.OBERYN_PROJECT_KEY;

export const options = {
  scenarios: {
    sustained_ingest: {
      executor: "constant-arrival-rate",
      rate: Number(__ENV.TARGET_RPS || 1000),
      timeUnit: "1s",
      duration: __ENV.DURATION || "10m",
      preAllocatedVUs: Number(__ENV.PREALLOCATED_VUS || 200),
      maxVUs: Number(__ENV.MAX_VUS || 2000),
      gracefulStop: "30s",
    },
  },
  thresholds: {
    http_req_duration: ["p(99)<100"],
    http_req_failed: ["rate<0.01"],
    checks: ["rate>0.99"],
  },
};

export function setup() {
  if (!projectId || !projectKey) {
    throw new Error("Set OBERYN_PROJECT_ID and OBERYN_PROJECT_KEY");
  }
  const ready = http.get(`${baseUrl}/health/ready`, { tags: { endpoint: "readiness" } });
  if (ready.status !== 200) {
    throw new Error(`API is not ready: /health/ready returned ${ready.status}`);
  }
  return { projectId, projectKey };
}

export default function (data) {
  const eventId = crypto.randomUUID();
  const payload = {
    schema_version: 1,
    event_id: eventId,
    timestamp: new Date().toISOString(),
    platform: "javascript",
    level: "error",
    release: __ENV.RELEASE || "loadtest@1.0.0",
    environment: "load-test",
    exception: {
      type: "LoadTestError",
      value: "Synthetic sustained-ingest benchmark event",
      stacktrace: [{
        filename: "https://loadtest.invalid/app.js",
        function: "loadTestHandler",
        lineno: 42,
        colno: 17,
        in_app: true,
      }],
    },
    breadcrumbs: [],
    user: { id: `loadtest-${__VU % 1000}` },
    tags: { source: "k6", scenario: "sustained-ingest" },
    request: { url: "https://loadtest.invalid/", method: "GET" },
  };
  const response = http.post(
    `${baseUrl}/api/${data.projectId}/envelope`,
    JSON.stringify(payload),
    {
      headers: {
        "Content-Type": "application/json",
        "X-Oberyn-Key": data.projectKey,
        "X-Oberyn-Project-Id": data.projectId,
      },
      tags: { endpoint: "ingest" },
      timeout: "10s",
    },
  );
  statuses.add(1, { status: String(response.status) });
  check(response, {
    "ingest accepted (202)": (r) => r.status === 202,
  });
}

export function handleSummary(data) {
  return {
    [__ENV.SUMMARY_FILE || "loadtest/results/k6-summary.json"]: JSON.stringify(data, null, 2),
  };
}
