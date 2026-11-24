const { createModelProvider } = require('./model-provider.cjs');

// 直接注入预先准备的教学资料，展示知识库上下文的作用；本例不实现检索或向量数据库。
module.exports = {
  description: '知识库问答：虚构政策的无资料与有资料对比（2 题，不含检索）',
  prompts: [
    { id: 'file://prompts/rag-without-context.txt', label: 'rag-without-context' },
    { id: 'file://prompts/rag-with-context.txt', label: 'rag-with-context' },
  ],
  providers: [createModelProvider('TAMS 知识库问答')],
  tests: 'file://tests/rag-cases.yaml',
  sharing: false,
  defaultTest: {
    assert: [
      {
        type: 'is-json',
        metric: '问答格式',
        value: {
          type: 'object',
          required: ['answer', 'unknown', 'sourceId'],
          additionalProperties: false,
          properties: {
            answer: { type: 'string', minLength: 1 },
            unknown: { type: 'boolean' },
            sourceId: { type: ['string', 'null'] },
          },
        },
      },
    ],
  },
  evaluateOptions: { maxConcurrency: 2 },
};
