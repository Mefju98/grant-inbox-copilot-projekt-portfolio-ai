import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_n8n_workflow_has_connected_webhook_api_escalation_and_response_nodes():
    workflow = json.loads((ROOT / "automation/n8n/grant-inbox-copilot.json").read_text())
    nodes = {node["name"]: node for node in workflow["nodes"]}

    assert workflow["active"] is False
    assert nodes["Receive synthetic inquiry"]["type"] == "n8n-nodes-base.webhook"
    assert nodes["Call Grant Inbox API"]["parameters"]["url"].endswith("/triage")
    assert nodes["Needs human review?"]["type"] == "n8n-nodes-base.if"
    assert "Return review-required result" in nodes
    assert "Return triage result" in nodes
    assert "Call Grant Inbox API" in workflow["connections"]["Receive synthetic inquiry"]["main"][0][0]["node"]
    assert "Needs human review?" in workflow["connections"]["Call Grant Inbox API"]["main"][0][0]["node"]


def test_power_automate_guide_documents_localhost_limit_and_schema():
    guide = (ROOT / "automation/power-automate/README.md").read_text(encoding="utf-8")

    assert "not an importable Power Automate package" in guide
    assert "127.0.0.1" in guide
    assert '"inquiry_id"' in guide
    assert "result.escalate" in guide
