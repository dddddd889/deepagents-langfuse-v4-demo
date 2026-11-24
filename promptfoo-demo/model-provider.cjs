/** 创建教学示例共用的真实模型配置，凭证始终通过环境变量传入。 */
function createModelProvider(label) {
  // 默认使用本次指定的 TAMS；保留显式选择 OpenAI 兼容接口的入口。
  const useTams = process.env.MODEL_PROVIDER !== 'openai';
  const model = useTams
    ? process.env.TAMS_MODEL || 'volcano/deepseek-v4-1-flash-260910'
    : process.env.OPENAI_MODEL || 'gpt-4.1-mini';
  return {
    id: `openai:chat:${model}`,
    label: label || (useTams ? 'TAMS DeepSeek Flash' : 'OpenAI 兼容模型'),
    config: {
      apiBaseUrl: useTams
        ? process.env.TAMS_BASE_URL || 'https://dev-cn.tensorart.net/v2/openapi/v1'
        : process.env.OPENAI_BASE_URL || 'https://api.openai.com/v1',
      apiKeyEnvar: useTams ? 'TAMS_TOKEN' : 'OPENAI_API_KEY',
      omitDefaults: true,
      // 只对最终回答做 JSON 和语义检查，避免把接口单独返回的思考内容拼进答案。
      showThinking: false,
      temperature: 0,
      max_tokens: 2048,
    },
  };
}

module.exports = { createModelProvider };
