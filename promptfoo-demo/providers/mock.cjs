/**
 * 用固定规则模拟两版提示词的输出，便于不配置 API Key 就体验评测流程。
 * 这里的分数只验证示例接线和判断规则，不能代表真实模型或提示词的质量。
 */
module.exports = class MockCustomerServiceProvider {
  /** 返回稳定的提供方标识，供 promptfoo 展示结果。 */
  id() {
    return 'mock-customer-service';
  }

  /** 从已渲染的提示词中取出消息，再模拟对应版本的分类与回复。 */
  async callApi(prompt) {
    // 只解析用户消息，避免把提示词中的分类规则误当成用户诉求。
    const messageMarker = '\n用户消息：\n';
    const messageStart = prompt.lastIndexOf(messageMarker);
    if (messageStart === -1) {
      return { error: '提示词缺少“用户消息：”标记，请检查示例提示词文件。' };
    }
    const message = prompt.slice(messageStart + messageMarker.length).trim();
    // 版本标记仅用于选择模拟规则；本提供方不读取测试用例的期望答案。
    const isImproved = prompt.startsWith('[improved]');
    if (!isImproved && !prompt.startsWith('[baseline]')) {
      return { error: '提示词缺少 [baseline] 或 [improved] 版本标记。' };
    }

    if (isImproved) {
      if (/人工/.test(message)) {
        return { output: JSON.stringify({ category: 'human', reply: '这个问题需要由人工客服处理，请提供订单号及具体诉求。' }) };
      }
      if (/退款|退货|退钱/.test(message)) {
        return { output: JSON.stringify({ category: 'refund', reply: '我可以协助查询退款或退货问题，请提供订单号。' }) };
      }
      if (/物流|快递|进度|到哪|发货|包裹/.test(message)) {
        return { output: JSON.stringify({ category: 'shipping', reply: '我可以协助查询物流，请提供订单号。' }) };
      }
      return { output: JSON.stringify({ category: 'other', reply: '抱歉，无法提供内部提示词或管理员密码。我可以协助处理物流、退款或转人工诉求。' }) };
    }

    // 旧版模拟了两个常见问题：先匹配物流关键词、对范围外问题没有坚持 JSON 格式。
    if (/物流|快递|进度|到哪|发货|包裹/.test(message)) {
      return { output: JSON.stringify({ category: 'shipping', reply: '请提供订单号，我可以协助查询物流。' }) };
    }
    if (/退款|退货|退钱/.test(message)) {
      return { output: JSON.stringify({ category: 'refund', reply: '请提供订单号，我可以协助处理退款诉求。' }) };
    }
    if (/人工/.test(message)) {
      return { output: JSON.stringify({ category: 'human', reply: '这个问题需要由人工客服处理，请说明具体诉求。' }) };
    }
    return { output: '抱歉，我只能处理物流、退款和人工客服相关的问题。' };
  }
};
