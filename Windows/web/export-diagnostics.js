const FINAL = new Set(['done', 'paused', 'preserved', 'error', 'failed', 'cancelled']);
const rowsOf = value => Array.isArray(value) ? value : [];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const STAGES = {
  source: '读取来源', source_selection: '选择来源', source_preflight: '检查来源',
  selection: '读取本次选择', player_data: '写入球员数据', player_art: '编译球员美术资源',
  model_binding: '准备脸模骨架', private_motion: '生成自制动态卡面',
  coaches: '写入教练', coach_models: '生成教练模型', packs: '生成卡包',
  pack_covers: '生成卡包封面', content_sync: '补齐内容资源', iostore: '整理美术资源',
  runtime: '写入游戏启动组件与运行逻辑', native_binding: '写入原生接口',
  tournaments: '写入锦标赛', packaging: '生成安装包', publication: '保存生成文件',
  controller: '写入手柄设置', display: '写入画面设置', frame_rate: '写入帧率设置',
  request: '提交操作请求',
};

export function exportIssues(state = {}) {
  const result = state.result || {};
  const rows = [...(rowsOf(state.diagnostics).length ? state.diagnostics : rowsOf(state.warnings)),
    ...rowsOf(result.diagnostics), ...rowsOf(result.warnings), ...rowsOf(result.report?.diagnostics), ...rowsOf(result.report?.warnings)];
  for (const pack of rowsOf(result.report?.packs)) {
    for (const member of rowsOf(pack.skipped_members)) {
      rows.push({code: 'preserve_item', item: member.card_id || member.id || member.name,
        message: `卡包“${pack.name || '未命名卡包'}”中的 ${member.name || member.card_id || member.id || '球员卡'} 未写入。`,
        stage: 'packs', detail: member.reason || member.error || '当前来源版本禁用了这张球员卡。'});
    }
  }
  const seen = new Set();
  return rows.filter(row => row && typeof row === 'object').map(row => ({
    code: String(row.code || 'warning'), item: String(row.item || ''),
    message: String(row.message || '本次导出有项目未能完成。'),
    stage: String(row.stage || ''), detail: String(row.detail || ''),
    errorType: String(row.error_type || ''),
  })).filter(row => {
    const key = JSON.stringify([row.code, row.item, row.message, row.stage, row.detail]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function exportIssueSummary(state = {}) {
  if (!FINAL.has(state.phase)) return '';
  const issues = exportIssues(state);
  if (!issues.length) return '';
  const blocked = issues.find(row => row.code === 'blocked');
  return `有 ${issues.length} 项提示或报错。` + (blocked?.detail ? `原因：${blocked.detail}` : '请查看详情。');
}

export function renderExportDiagnostics(state = {}) {
  if (!FINAL.has(state.phase)) return '';
  const issues = exportIssues(state);
  if (!issues.length) return '';
  return `<section class="migration-status error" data-export-diagnostics aria-label="导出提示与报错信息">
<h3>本次导出提示与报错（${issues.length} 项）</h3><ol>${issues.map(row => `<li>
<strong>${esc(row.message)}</strong>${row.stage ? `<p>步骤：${esc(STAGES[row.stage] || row.stage)}</p>` : ''}
${row.item ? `<p>项目：${esc(row.item)}</p>` : ''}${row.errorType ? `<p>报错类型：${esc(row.errorType)}</p>` : ''}
${row.detail ? `<pre style="white-space:pre-wrap;overflow-wrap:anywhere;font:inherit">${esc(row.detail)}</pre>` : ''}
</li>`).join('')}</ol></section>`;
}
