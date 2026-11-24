"""完整追踪样例的固定订单与历史事实；不读取环境变量或连接外部服务。"""

# 演示订单是查询服务的固定数据，评估时仍以本次 MCP 实际返回值为准。
DEMO_ORDER = {
    "order_id": "ORD-1001",
    "found": True,
    "status": "已发货",
    "carrier": "顺丰",
    "tracking_no": "SF-DEMO-1001",
    "items": ["无线鼠标"],
}
CONTACT_CHANNEL = "在线客服"
ANSWER_FIELDS = ("order_id", "status", "carrier", "tracking_no", "contact_channel")
