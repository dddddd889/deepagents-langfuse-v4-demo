const { spawnSync } = require('node:child_process');
const { mkdirSync } = require('node:fs');
const path = require('node:path');

// 固定工作目录，让题目、提示词和输出文件始终相对于这个独立示例查找。
const projectRoot = path.resolve(__dirname, '..');
process.chdir(projectRoot);

// 将 promptfoo 的数据、日志和缓存都留在本示例内，并关闭遥测。
process.env.PROMPTFOO_CONFIG_DIR = path.join(projectRoot, '.promptfoo');
process.env.PROMPTFOO_CACHE_PATH = path.join(projectRoot, '.promptfoo', 'cache');
process.env.PROMPTFOO_LOG_DIR = path.join(projectRoot, '.promptfoo', 'logs');
process.env.PROMPTFOO_DISABLE_TELEMETRY = '1';

// 版本由 package.json 固定，因此这里使用该版本声明的命令入口。
const cliPath = path.join(projectRoot, 'node_modules/promptfoo/dist/src/entrypoint.js');
const mode = process.argv[2];
const extraArgs = process.argv.slice(3);
let cliArgs;

if (mode === 'view') {
  cliArgs = ['view', '--port', '15500', '--no'];
} else if (['demo', 'improved', 'live'].includes(mode)) {
  if (mode === 'live' && (!process.env.OPENAI_API_KEY || process.env.OPENAI_API_KEY === '请在本地填写')) {
    console.error('请复制 .env.example 为 .env，并在本地填写 OPENAI_API_KEY 后再运行真实模型评测。');
    process.exit(1);
  }
  mkdirSync(path.join(projectRoot, 'output'), { recursive: true });
  cliArgs = [
    'eval', '-c', mode === 'live' ? 'promptfooconfig.live.cjs' : 'promptfooconfig.cjs',
    '--no-cache', '-o', `output/${mode}.json`, `output/${mode}.html`,
  ];
  if (mode === 'improved') {
    // 从文件加载提示词后，标签会附加文件路径，所以按版本前缀筛选。
    cliArgs.push('--filter-prompts', '^improved');
  }
} else {
  console.error('用法：node scripts/run.cjs demo|improved|live|view');
  process.exit(1);
}

// 透传退出码：断言失败应被自动化流程识别，不能因演示需要而伪装成通过。
const result = spawnSync(process.execPath, [cliPath, ...cliArgs, ...extraArgs], { stdio: 'inherit' });
if (result.error) {
  console.error(`启动 promptfoo 失败：${result.error.message}`);
}
process.exit(result.status ?? 1);
