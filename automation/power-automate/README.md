# Power Automate equivalent (manual setup)

This is a configuration walkthrough, not an importable Power Automate package. It demonstrates the same synthetic inquiry handoff as the n8n workflow.

## Flow outline

`When an HTTP request is received` → `HTTP` (POST to Grant Inbox API) → `Parse JSON` → `Condition: result.escalate is true` → `Response` (return the API result and make the review state visible to the caller).

Power Automate's cloud flow cannot normally reach a service bound to `127.0.0.1` on your PC. For a real run, use an approved reachable development endpoint or an appropriately configured on-premises data gateway. Do not publish this demo API openly. The n8n workflow uses `host.docker.internal` for the common case where n8n runs in Docker on the same Windows computer; change the URL if your setup differs.

## 1. Create the trigger

1. Create an **Automated cloud flow** and select **When an HTTP request is received**.
2. Use the following request JSON schema, then save the flow so the trigger can generate its URL:

```json
{
  "type": "object",
  "properties": {
    "inquiry_id": { "type": "string" },
    "message": { "type": "string" },
    "received_at": { "type": ["string", "null"] }
  },
  "required": ["inquiry_id", "message"]
}
```

3. For this portfolio project, send only synthetic data. Treat the trigger URL as a secret if the tenant's trigger authentication policy makes it a bearer credential; do not commit it.

## 2. Call the API

Add an **HTTP** action:

- Method: `POST`
- URI: the network-reachable API endpoint ending in `/triage`
- Header: `Content-Type` = `application/json`
- Body: use the trigger's complete body (dynamic content `Body`, or expression `triggerBody()`)

For a cloud Power Automate flow, `http://127.0.0.1:8000/triage` points to Microsoft's cloud runtime, not your Windows PC. Configure a secure, approved path to the API before trying to run this action.

## 3. Parse and surface the review decision

Add **Parse JSON**, with Content set to the HTTP action's body. Generate the schema from a successful synthetic `/triage` response, since the model output and nullable fields can vary. Then add a **Condition** using `body('Parse_JSON')?['result']?['escalate']` equal to Boolean `true`.

In each branch add a **Response** action (or replace the response with an internal queue/task action once you configure and authorize that connector). Return the full API body to the caller. In the Yes branch, make the `escalate` flag and `escalation_reasons` prominent for the human reviewer. Do not send automatic grant recommendations to prospects from this demo.

## Test payload

```json
{
  "inquiry_id": "pa-demo-001",
  "message": "We are a small company in North and need funding for an R&D prototype."
}
```

The expected response is the API's JSON envelope with `result` and `llm`. An uncertain result should have `result.escalate: true`; the workflow must preserve that value for human review.

## Why this stays a manual setup

Power Automate flow exports are environment- and connector-dependent. A hand-authored export can contain tenant-specific identifiers and appear portable while failing to import. This walkthrough keeps the portfolio artifact honest and lets you demonstrate the equivalent nodes without storing tenant credentials or URLs in this repository.
