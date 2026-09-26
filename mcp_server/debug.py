"""Debug logging for the MCP server. Enabled with MCP_DEBUG=true.

When on, every AWS API call is logged with its operation, request params and
the full parsed response (or the error), plus which credentials/region/endpoint
the client is using. Secret keys and session tokens are never logged.
"""
import functools
import json
import logging
import os

from mcp.server.mcpserver.exceptions import ToolError

logger = logging.getLogger("cost_janitor")

# Cap per log line so a large Describe* response can't flood the log; 0 = unlimited.
MAX_CHARS = int(os.environ.get("MCP_DEBUG_MAX_CHARS", "20000"))


def enabled() -> bool:
    return os.environ.get("MCP_DEBUG", "").lower() in ("1", "true", "yes")


def configure() -> None:
    logging.basicConfig(
        level=logging.DEBUG if enabled() else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.setLevel(logging.DEBUG if enabled() else logging.INFO)
    # botocore's own DEBUG output includes signed request headers; we log the
    # API-level detail ourselves instead.
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)


def dump(obj) -> str:
    text = json.dumps(obj, default=str, sort_keys=True)
    if MAX_CHARS and len(text) > MAX_CHARS:
        return f"{text[:MAX_CHARS]}... [truncated, {len(text)} chars total]"
    return text


def _mask(value: str | None) -> str:
    return f"{value[:4]}...{value[-4:]}" if value and len(value) > 8 else "<none>"


def attach_aws_logging(client) -> None:
    """Log every API call made through this boto3 client."""
    if not enabled():
        return

    creds = client._request_signer._credentials  # noqa: SLF001 - only way to see which key is in use
    logger.debug(
        "AWS client created: service=%s region=%s endpoint=%s access_key_id=%s",
        client.meta.service_model.service_name,
        client.meta.region_name,
        client.meta.endpoint_url,
        _mask(getattr(creds, "access_key", None)),
    )

    def on_params(params, model, **_):
        logger.debug("AWS -> %s.%s params=%s", model.service_model.service_name, model.name, dump(params))

    def on_response(http_response, parsed, model, **_):
        meta = parsed.get("ResponseMetadata", {})
        logger.debug(
            "AWS <- %s.%s http=%s request_id=%s attempts=%s body=%s",
            model.service_model.service_name,
            model.name,
            meta.get("HTTPStatusCode"),
            meta.get("RequestId"),
            meta.get("RetryAttempts"),
            dump({k: v for k, v in parsed.items() if k != "ResponseMetadata"}),
        )

    def on_error(exception, operation_model, **_):
        logger.error(
            "AWS !! %s.%s failed: %s: %s response=%s",
            operation_model.service_model.service_name,
            operation_model.name,
            type(exception).__name__,
            exception,
            dump(getattr(exception, "response", {})),
        )

    events = client.meta.events
    events.register("provide-client-params.*.*", on_params)
    events.register("after-call.*.*", on_response)
    events.register("after-call-error.*.*", on_error)


def logged_tool(fn):
    """Log tool entry/exit. Crashes are logged with their full traceback here,
    because the MCP SDK deliberately hides a crash's text from the client and
    only reports a generic 'Error executing tool'."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        logger.info("tool %s called: %s", fn.__name__, dump(kwargs or args))
        try:
            result = fn(*args, **kwargs)
        except ToolError as exc:
            logger.warning("tool %s rejected: %s", fn.__name__, exc)
            raise
        except Exception:
            logger.exception("tool %s crashed", fn.__name__)
            raise
        logger.debug("tool %s result: %s", fn.__name__, dump(result))
        return result

    return wrapper
