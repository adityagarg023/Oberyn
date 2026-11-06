# @oberyn/sdk

A dependency-free TypeScript SDK for sending Oberyn events.

## Usage

```ts
import { init, captureException, captureMessage } from "@oberyn/sdk";

init({
  dsn: "https://<public-key>@ingest.oberyn.dev/<project-id>",
  environment: "production",
  release: "web@1.0.0",
  sampleRate: 1,
  beforeSend(event) {
    // Return null to drop an event.
    return event;
  },
});

try {
  riskyOperation();
} catch (error) {
  captureException(error);
}

captureMessage("Checkout started");
```

The DSN public key and project ID are sent in headers. Events are POSTed to
`/api/v1/events` as Event V1 JSON. The SDK intentionally does not install
global error listeners, and transport failures are swallowed so they cannot
become new SDK-captured errors.

## Development

- `npm run test -w @oberyn/sdk`
- `npm run typecheck -w @oberyn/sdk`
- `npm run build -w @oberyn/sdk`

The bundle size budget is <5 KB gzipped; verify the generated artifact after
building with the package's production minification settings.
