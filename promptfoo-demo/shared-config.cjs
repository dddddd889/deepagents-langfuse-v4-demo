// 两种运行方式共用提示词、题目和评分规则，只替换回答问题的接口。
module.exports = {
  description: '电商客服分类：用六条问题对比旧版和改进版提示词',
  prompts: [
    { id: 'file://prompts/baseline.txt', label: 'baseline' },
    { id: 'file://prompts/improved.txt', label: 'improved' },
  ],
  tests: 'file://tests/cases.yaml',
  sharing: false,
  defaultTest: {
    assert: [
      {
        type: 'is-json',
        metric: '输出格式',
        value: {
          type: 'object',
          required: ['category', 'reply'],
          additionalProperties: false,
          properties: {
            category: { type: 'string', enum: ['shipping', 'refund', 'human', 'other'] },
            reply: { type: 'string', minLength: 1 },
          },
        },
      },
    ],
  },
  evaluateOptions: { maxConcurrency: 2 },
};
