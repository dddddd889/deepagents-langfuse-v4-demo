// 默认只运行本地规则模拟，不读取模型凭证，也不调用模型接口。
module.exports = {
  ...require('./shared-config.cjs'),
  providers: ['file://providers/mock.cjs'],
};
