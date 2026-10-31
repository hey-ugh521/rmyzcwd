"""Phoenix / OTLP 连通性冒烟测试。

用途：确认「本机 -> Phoenix(4318) -> 落库 -> 可查询」这条链路是通的，
之后再把真实的 MCP 工具调用 / Cortex 事件接进来。
"""
import os
import time

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

OTLP = os.environ.get("OTLP_ENDPOINT", "http://127.0.0.1:6006/v1/traces")

resource = Resource.create({"service.name": "mobius-smoke", "service.version": "0.1"})
provider = TracerProvider(resource=resource)
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=OTLP)))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("mobius.smoke")

with tracer.start_as_current_span("mcp.tool.call") as span:
    span.set_attribute("tool.name", "comfyui_list_models")
    span.set_attribute("tool.source", "mcp")
    span.set_attribute("mobius.session", "smoke-test")
    with tracer.start_as_current_span("gateway.http") as child:
        child.set_attribute("http.route", "/mcp")
        child.set_attribute("http.status_code", 200)
        time.sleep(0.05)
    span.set_attribute("tool.duration_ms", 52)

provider.force_flush(timeout_millis=5000)
print(f"已发送测试 span 到 {OTLP}")
