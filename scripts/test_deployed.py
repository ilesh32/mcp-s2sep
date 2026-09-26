"""Live test against the running container: real MCP client, real AWS, no mocks.

Usage: .venv/bin/python scripts/test_deployed.py [--skip-teardown] [url]

Token comes from MCP_AUTH_TOKEN (env or .env) or the .mcp_token file that
scripts/deploy_local.sh generates.

Unless --skip-teardown is given, resources returned by the scan (tagged
hackathon-demo=true only, at most 10) are REALLY deleted via execute_teardown,
then re-scanned to confirm they are gone.
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

args = [a for a in sys.argv[1:] if not a.startswith("--")]
SKIP_TEARDOWN = "--skip-teardown" in sys.argv
URL = args[0] if args else f"http://localhost:{os.environ.get('MCP_PORT', '8000')}/mcp"
REGION = os.environ.get("AWS_REGION", "us-east-1")
TOKEN = os.environ.get("MCP_AUTH_TOKEN") or (ROOT / ".mcp_token").read_text().strip()


def http_status(url: str, headers: dict[str, str] | None = None, data: bytes | None = None) -> int:
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        return urllib.request.urlopen(request, timeout=10).status
    except urllib.error.HTTPError as exc:
        return exc.code


def payload(result) -> dict:
    assert not result.is_error, result.content
    return result.structured_content or json.loads(result.content[0].text)


def resource_ids(scan: dict) -> list[str]:
    return [r["id"] for r in scan["orphaned_volumes"] + scan["idle_instances"]] + \
           [lb["arn"] for lb in scan["idle_load_balancers"]]


async def main() -> None:
    base = URL.rsplit("/", 1)[0]
    assert http_status(f"{base}/healthz") == 200
    print("PASS /healthz is reachable without auth")
    assert http_status(URL, data=b"{}", headers={"Content-Type": "application/json"}) == 401
    assert http_status(URL, data=b"{}", headers={"Authorization": "Bearer wrong"}) == 401
    print("PASS /mcp rejects missing and wrong bearer tokens (401)")

    http_client = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"})
    async with Client(streamable_http_client(URL, http_client=http_client)) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert tools == {"list_resources", "analyze_infrastructure", "execute_teardown"}, tools
        schema = next(t for t in (await client.list_tools()).tools if t.name == "execute_teardown").input_schema
        assert "dry_run" not in schema["properties"], "dry_run must not exist"
        print(f"PASS list_tools: {sorted(tools)} (no dry_run parameter)")

        wrong_region = await client.call_tool("analyze_infrastructure", {"region": "mars-north-1"})
        assert wrong_region.is_error and REGION in wrong_region.content[0].text, wrong_region.content
        print("PASS analyze_infrastructure rejects a region other than the configured one")

        inventory = payload(await client.call_tool("list_resources", {"region": REGION}))
        for key in ("instances", "volumes", "load_balancers"):
            assert key in inventory, f"missing {key}: {inventory}"
        print(f"PASS list_resources ({REGION}): " + ", ".join(f"{k}={len(inventory[k])}" for k in ("instances", "volumes", "load_balancers")))
        for inst in inventory["instances"]:
            print(f"     instance {inst['id']} {inst['state']} name={inst['name']} in_scope={inst['in_scope']}")

        scan = payload(await client.call_tool("analyze_infrastructure", {"region": REGION}))
        for key in ("orphaned_volumes", "idle_instances", "idle_load_balancers"):
            assert key in scan, f"missing {key}: {scan}"
        print(f"PASS analyze_infrastructure ({REGION}): " + ", ".join(f"{k}={len(scan[k])}" for k in scan if isinstance(scan[k], list)))

        bogus = await client.call_tool("execute_teardown", {"resource_ids": ["vol-00000000000000000"]})
        assert bogus.is_error and "not seen" in bogus.content[0].text, bogus.content
        print("PASS provenance guard rejected an id never returned by analyze_infrastructure")

        too_many = await client.call_tool("execute_teardown", {"resource_ids": [f"vol-{i:017d}" for i in range(11)]})
        assert too_many.is_error and "at most 10" in too_many.content[0].text, too_many.content
        print("PASS blast-radius cap rejected 11 ids")

        ids = resource_ids(scan)[:10]
        if SKIP_TEARDOWN:
            print("SKIP real teardown (--skip-teardown)")
        elif not ids:
            print("SKIP real teardown: scan found no resources tagged hackathon-demo=true")
        else:
            print(f"DELETING {len(ids)} real resources: {ids}")
            result = payload(await client.call_tool("execute_teardown", {"resource_ids": ids}))
            print(f"teardown result: {json.dumps(result, indent=2)}")
            assert not result["failed"], f"teardown reported failures: {result['failed']}"
            after = set(resource_ids(payload(await client.call_tool("analyze_infrastructure", {"region": REGION}))))
            still_there = {d["id"] for d in result["deleted"]} & after
            assert not still_there, f"deleted resources still reported by scan: {still_there}"
            print(f"PASS real teardown: deleted={len(result['deleted'])} skipped={len(result['skipped'])}, re-scan confirms gone")

    print("ALL CHECKS PASSED")


asyncio.run(main())
