const { mkdirSync, writeFileSync } = require('node:fs');
const path = require('node:path');

// 固定输出到示例目录，方便在本机或 Compose 的 /workspace 中使用同一条命令。
const projectRoot = path.resolve(__dirname, '..');
const outputDirectory = path.join(projectRoot, 'output');
const outputFile = path.join(outputDirectory, 'redteam.config.json');

// 0.126.0 生成器保存结果时会再次按 YAML 读取原配置；JSON 可同时被配置加载器和 YAML 解析器读取。
// 共用工厂仅写入 apiKeyEnvar 环境变量名，序列化后的配置不包含实际凭证值。
const redteamConfig = require('../promptfooconfig.redteam.cjs');
mkdirSync(outputDirectory, { recursive: true });
writeFileSync(outputFile, `${JSON.stringify(redteamConfig, null, 2)}\n`, 'utf8');
console.log('已导出 output/redteam.config.json，可继续生成安全测试用例。');
