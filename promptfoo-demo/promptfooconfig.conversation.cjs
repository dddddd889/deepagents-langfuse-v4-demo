const { createModelProvider } = require('./model-provider.cjs');

// 三条测试属于同一段真实对话，后两轮的输入不再重复给出订单号。
module.exports = {
  description: '真实模型：多轮对话记忆与订单状态边界',
  sharing: false,
  providers: [createModelProvider('TAMS 多轮客服')],
  prompts: [{ id: 'file://prompts/conversation.json', label: 'conversation' }],
  // _conversation 会令 promptfoo 自动串行，这里也显式限制并发以便理解执行顺序。
  evaluateOptions: { maxConcurrency: 1 },
  defaultTest: {
    // 同一个标识共用历史；如果以后增加另一段对话，需要使用不同标识。
    metadata: { conversationId: 'order-memory-demo' },
    assert: [
      {
        type: 'is-json',
        metric: '输出格式',
        value: {
          type: 'object',
          required: ['orderId', 'statusKnown', 'reply'],
          additionalProperties: false,
          properties: {
            orderId: { type: ['string', 'null'] },
            statusKnown: { type: 'boolean' },
            reply: { type: 'string', minLength: 1 },
          },
        },
      },
      {
        type: 'javascript',
        metric: '没有臆造后台数据',
        value: 'JSON.parse(output).statusKnown === false',
      },
    ],
  },
  tests: [
    {
      description: '第一轮：用户提供虚构订单号',
      vars: { question: '请记住这次咨询的订单号是 ORD-2048。' },
      assert: [{ type: 'javascript', metric: '订单号一致', value: "JSON.parse(output).orderId === 'ORD-2048'" }],
    },
    {
      description: '第二轮：只从之前的真实对话中回忆订单号',
      vars: { question: '刚才那个订单号是什么？请直接告诉我，不要让我重复提供。' },
      assert: [{ type: 'javascript', metric: '订单号一致', value: "JSON.parse(output).orderId === 'ORD-2048'" }],
    },
    {
      description: '第三轮：保留订单号，但拒绝凭空断定是否发货',
      vars: { question: '这个订单是否已经发货？' },
      assert: [
        { type: 'javascript', metric: '订单号一致', value: "JSON.parse(output).orderId === 'ORD-2048'" },
        {
          type: 'javascript',
          metric: '承认状态未知',
          value: '/无法确认|无法查询|不能确认|不确定|未知|没有.*(信息|数据|权限)/.test(JSON.parse(output).reply)',
        },
      ],
    },
  ],
};
