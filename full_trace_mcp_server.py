"""通过真实 MCP stdio 协议提供固定订单查询，不连接真实订单系统。"""

from mcp.server import MCPServer
from pydantic import BaseModel

from full_trace_data import DEMO_ORDER

server = MCPServer("demo-orders", log_level="WARNING")


class OrderLookupResult(BaseModel):
    """明确 MCP 的订单返回结构，未找到时物流字段保持空值。"""

    order_id: str
    found: bool
    status: str | None = None
    carrier: str | None = None
    tracking_no: str | None = None
    items: list[str] | None = None
    message: str | None = None


@server.tool(structured_output=True)
def query_order(order_id: str) -> OrderLookupResult:
    """根据订单号查询演示订单，未知订单明确返回未找到。"""
    if order_id == DEMO_ORDER["order_id"]:
        return OrderLookupResult(**DEMO_ORDER)
    return OrderLookupResult(order_id=order_id, found=False, message="未找到订单")


if __name__ == "__main__":
    # 标准输出专供协议通信，业务日志不得 print 到标准输出。
    server.run(transport="stdio")
