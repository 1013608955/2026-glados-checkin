// SMAI.AI 每日自动签到
// 环境变量: SMAI_SESSION (必需), SMAI_USER_ID (可选, 默认 1207)

const https = require('https');

const SESSION = process.env.SMAI_SESSION;
const USER_ID = process.env.SMAI_USER_ID || '1207';

if (!SESSION) {
  console.error('❌ 错误: 请设置环境变量 SMAI_SESSION');
  process.exit(1);
}

function request(method, path, body = null) {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: 'api.smai.ai',
      port: 443,
      path,
      method,
      headers: {
        'Accept': 'application/json',
        'new-api-user': USER_ID,
        'Cookie': `session=${SESSION}`,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://api.smai.ai/console/checkin',
        'Origin': 'https://api.smai.ai'
      }
    };

    if (body) {
      const data = JSON.stringify(body);
      options.headers['Content-Type'] = 'application/json';
      options.headers['Content-Length'] = Buffer.byteLength(data);
    }

    const req = https.request(options, (res) => {
      let raw = '';
      res.on('data', chunk => raw += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(raw)); }
        catch { resolve({ raw }); }
      });
    });

    req.on('error', reject);
    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

(async () => {
  const now = new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
  console.log(`\n🦞 SMAI.AI 自动签到 - ${now}`);
  console.log('─'.repeat(40));

  // 查询状态
  const year = new Date().getFullYear();
  const stats = await request('GET', `/api/user/checkin?year=${year}`);

  if (stats.success && stats.data?.checked_in_today) {
    console.log('✅ 今天已签到');
    console.log(`📊 总签到: ${stats.data.stats.total_checkins} 天 | 总额度: ${stats.data.stats.total_quota}`);
    setGitHubOutput('status', 'already_checked');
    process.exit(0);
  }

  // 执行签到
  console.log('📝 正在签到...');
  const result = await request('POST', '/api/user/checkin', {});

  if (result.success) {
    console.log('✅ 签到成功！');
    setGitHubOutput('status', 'success');
  } else {
    console.log(`⚠️ ${result.message || '签到响应异常'}`);
    setGitHubOutput('status', result.message || 'failed');
  }

  // 显示统计
  const newStats = await request('GET', `/api/user/checkin?year=${year}`);
  if (newStats.success && newStats.data) {
    const s = newStats.data.stats;
    console.log(`\n📊 签到统计:`);
    console.log(`   总天数: ${s.total_checkins}`);
    console.log(`   总额度: ${s.total_quota}`);
    if (s.records?.[0]) {
      console.log(`   最新: ${s.records[0].checkin_date} → +${s.records[0].quota_awarded}`);
    }
  }

  console.log('─'.repeat(40));
})();

function setGitHubOutput(name, value) {
  const fs = require('fs');
  const output = process.env.GITHUB_OUTPUT;
  if (output) {
    fs.appendFileSync(output, `${name}=${value}\n`);
  }
}
