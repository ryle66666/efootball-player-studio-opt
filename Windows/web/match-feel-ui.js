import {t} from './i18n.js';

const presets = {
  original: ['原版', '恢复原版比赛常量与动作。搓射旋转 ×1.50，旋转量达到 0.70 时生效。'],
  workshop: ['工坊版', '沿用之前工坊的 V8 比赛调校。搓射旋转 ×1.75，门槛 0.50。'],
  custom: ['自定义', '按原版和工坊参考值，直接填写要用的数值。'],
};
const groups = [
  ['接球与转身', {
    receive_reaction: '本方地滚球接球反应时间',
    stop_input: '停球输入窗口',
    trap_cancel_frame: '停球动作取消帧',
    open_body: '接球打开身体最大值',
  }],
  ['地面传球调校', {
    ground_speed_min: '地面传球速度下限系数',
    ground_speed_max: '地面传球速度上限系数',
  }],
  ['长传与传中', {
    loft_gauge_distance: '长传满蓄力距离',
    cross_speed_adjustment: '传中速度修正',
  }],
  ['球的飞行、滚动与反弹', {
    magnus: '空中弧线系数',
    natural_roll: '自然滚动系数',
    stop_speed: '停球速度门槛',
    bounce_rate_0: '反弹系数 [0]',
    bounce_friction_speed: '反弹摩擦速度上限',
  }],
  ['搓射', {
    spin_factor: '搓射旋转倍率',
    spin_threshold: '出球旋转门槛',
  }],
];

function reference(key, number) {
  return key.startsWith('spin_') ? number.toFixed(2) : number;
}

export class MatchFeelUI {
  constructor(api, toast) {
    this.api = api;
    this.toast = toast;
  }

  render() {
    return '<section class="panel"><div class="panel-body" id="match-feel-body" aria-live="polite"></div></section>';
  }

  async activate(page) {
    if (page !== 'match-feel') return;
    const root = document.querySelector('#match-feel-body');
    root.textContent = t('正在读取设置…');
    const result = await this.api('tools/match-feel', {});
    if (!root.isConnected) return;
    this.state = result;
    this.draw(root);
  }

  draw(root) {
    const {value, supported, fields} = this.state;
    if (!supported) {
      root.textContent = t('当前来源需要使用 V17 比赛适配。');
      return;
    }
    root.innerHTML = `<p class="note">${t('保存后，重新生成并安装游戏 APK 生效。')}</p>
      <div class="world-tabs" role="group" aria-label="${t('比赛手感档位')}">
        ${Object.entries(presets).map(([mode, [label]]) =>
          `<button type="button" class="button ${mode === value.mode ? 'primary' : 'secondary'}" data-feel-mode="${mode}" aria-pressed="${mode === value.mode}">${t(label)}</button>`).join('')}
      </div>
      <p class="note">${t(presets[value.mode][1])}</p>
      ${value.mode === 'custom' ? `<form id="match-feel-form">
        ${groups.map(([group, entries]) => `<fieldset class="match-feel-group">
          <legend>${t(group)}</legend>
          <div class="field-grid">${Object.entries(entries).map(([key, label]) => {
            const field = fields[key];
            return `<label class="field" data-feel-field="${key}"><span>${t(label)}</span>
              <input class="input" required type="number" name="${key}" min="${field.min}" max="${field.max}"
                step="${field.step}" value="${reference(key, field.current)}">
              <small>${t('原版参考值')} <b data-feel-reference="original">${reference(key, field.original)}</b>
                · ${t('工坊参考值')} <b data-feel-reference="workshop">${reference(key, field.workshop)}</b></small></label>`;
          }).join('')}</div>`).join('')}
        <p class="note">${t('搓射的原版参考值来自工坊的「原版」档。')}</p>
        ${value.custom.legacy_groups ? `<p class="note">${t('已保留旧版比例设置中的其他参数。恢复自定义默认值会一并清除它们。')}</p>` : ''}
        <label class="switch-label"><input type="checkbox" name="response" ${value.custom.response ? 'checked' : ''}> ${t('工坊通用传控与射门修正')}</label>
        <label class="switch-label"><input type="checkbox" name="ai" ${value.custom.ai ? 'checked' : ''}> ${t('应用已启用的工坊 AI 设置')}</label>
        <div class="match-feel-actions">
          <button type="submit" class="button primary">${t('保存比赛手感')}</button>
          <button type="button" class="button secondary" data-feel-reset>${t('恢复自定义默认值')}</button>
        </div>
      </form>` : ''}
      <p class="note" id="match-feel-status" role="status"></p>`;
    root.querySelectorAll('[data-feel-mode]').forEach(button => {
      button.onclick = () => this.save(button.dataset.feelMode).catch(error => this.toast(error.message, true));
    });
    const form = root.querySelector('form');
    if (form) form.onsubmit = event => {
      event.preventDefault();
      this.save().catch(error => this.toast(error.message, true));
    };
    if (form) form.querySelector('[data-feel-reset]').onclick = () =>
      this.save('custom', true).catch(error => this.toast(error.message, true));
  }

  async save(mode = this.state?.value.mode, reset = false) {
    const root = document.querySelector('#match-feel-body');
    if (!root || !this.state?.supported || this.saving) return;
    const value = {mode, custom: reset ? {} : {...this.state.value.custom}};
    const form = root.querySelector('form');
    if (mode === 'custom' && form && !reset) {
      if (!form.reportValidity()) return;
      for (const [key, field] of Object.entries(this.state.fields)) {
        const number = form.elements.namedItem(key).valueAsNumber;
        if (number !== field.current) value.custom[key] = number;
      }
      for (const key of ['response', 'ai']) value.custom[key] = form.elements.namedItem(key).checked;
    }
    const controls = [...root.querySelectorAll('button,input')];
    controls.forEach(control => { control.disabled = true; });
    this.saving = true;
    try {
      this.state = await this.api('tools/match-feel', {value});
      if (root.isConnected) {
        this.draw(root);
        root.querySelector('#match-feel-status').textContent = t('比赛手感已保存');
      }
    } finally {
      this.saving = false;
      controls.forEach(control => { control.disabled = false; });
    }
  }
}
