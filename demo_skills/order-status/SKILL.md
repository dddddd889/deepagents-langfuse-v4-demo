---
name: order-status
description: 查询订单状态、物流，并按固定 JSON 格式保留用户的历史联系渠道。
---

查询订单时，必须先调用 `query_order_mcp`，只使用工具实际返回的字段。
从先前对话或压缩摘要中读取用户选择的联系渠道，不要猜测。

最终回答必须是一个 JSON 对象，不能有 Markdown 围栏、解释或其他字段：

```json
{
  "order_id": "工具返回的订单号",
  "status": "工具返回的状态",
  "carrier": "工具返回的快递公司",
  "tracking_no": "工具返回的快递单号",
  "contact_channel": "历史中用户选择的联系渠道"
}
```

如果没有找到订单，对应的状态和物流字段填 null；不编造预计到货时间。
