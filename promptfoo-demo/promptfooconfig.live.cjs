// 真实模型与模拟演示使用完全相同的试题，方便从学习流程切换到实际评测。
module.exports = {
  ...require('./shared-config.cjs'),
  providers: [
    {
      id: `openai:chat:${process.env.OPENAI_MODEL || 'gpt-4.1-mini'}`,
      config: {
        apiBaseUrl: process.env.OPENAI_BASE_URL || 'https://api.openai.com/v1',
        apiKeyEnvar: 'OPENAI_API_KEY',
        temperature: 0,
      },
    },
  ],
};
