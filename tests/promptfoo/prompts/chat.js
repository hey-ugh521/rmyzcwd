/**
 * promptfoo 提示词函数：把 systemFile 内容作为 system message，query 作为 user message。
 * 这样可以在同一套测试里对比「当前提示词」与「新提示词」。
 */
const fs = require('fs');
const path = require('path');

module.exports = async function ({ vars }) {
  const file = vars.systemFile || 'prompts/system_current.txt';
  const abs = path.isAbsolute(file) ? file : path.join(__dirname, '..', file);
  const system = fs.readFileSync(abs, 'utf8');
  return [
    { role: 'system', content: system },
    { role: 'user', content: vars.query || '' },
  ];
};
