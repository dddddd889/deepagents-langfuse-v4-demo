// 真实模型与模拟演示使用完全相同的试题，方便从学习流程切换到实际评测。
const { createModelProvider } = require('./model-provider.cjs');

module.exports = {
  ...require('./shared-config.cjs'),
  description: '真实模型：TAMS 客服提示词对比',
  providers: [createModelProvider()],
};
