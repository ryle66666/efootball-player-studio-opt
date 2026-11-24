const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const busy = new Set(['checking', 'downloading', 'preparing', 'installing']);

function progress(state) {
  if (!['downloading', 'preparing'].includes(state.phase)) return '';
  const done = Number(state.downloaded) || 0, total = Number(state.download_total) || 0;
  const mb = bytes => (bytes / 1048576).toFixed(1) + ' MB';
  const percentage = total ? Math.min(100, Math.floor(done / total * 100)) : null;
  return `<div class="update-progress"><progress aria-label="更新下载进度" ${total ? `max="${total}" value="${done}"` : ''}></progress><p class="note">${total ? `${percentage}% · ${mb(done)} / ${mb(total)}` : mb(done)}</p></div>`;
}

export class ReleaseUpdatesUI {
  constructor(context) {
    Object.assign(this, context);
    this.state = null;
    this.shown = null;
    document.addEventListener('click', event => this.click(event));
    document.addEventListener('change', event => {
      if (event.target.id === 'update-auto') this.save(event.target.checked).catch(error => this.toast(error.message, true));
    });
  }

  async activate(page) {
    this.active = page === 'home';
    if (this.active) await this.poll();
  }

  render() {
    const target = document.querySelector('#home-updates'), state = this.state;
    if (!target || !state) return;
    const open = target.querySelector('details')?.open;
    const disabled = busy.has(state.phase);
    const download = state.can_install ? '<button class="button primary" data-update-start>下载更新</button>'
      : '<button class="button primary" data-update-open>查看新版下载</button>';
    target.innerHTML = `<div class="panel-title"><h2>软件更新</h2><button class="button secondary" data-update-check ${disabled || state.phase === 'ready' ? 'disabled' : ''}>检查更新</button></div>
      <div class="panel-body"><label class="switch-label"><input type="checkbox" id="update-auto" ${state.settings.enabled ? 'checked' : ''} ${disabled ? 'disabled' : ''}>启动时自动检查新版（不强制）</label>
      <p class="note">当前版本 ${esc(state.current)}。${esc(state.message || '可手动检查 GitHub 正式版本。')}</p>${progress(state)}
      ${state.detail ? '<p class="note">失败原因：' + esc(state.detail) + '</p>' : ''}
      ${state.phase === 'available' ? download : ''}${state.phase === 'ready' ? '<button class="button primary" data-update-install>重启并更新</button>' : ''}
      <details class="export-optional" ${open ? 'open' : ''}><summary>更新来源</summary><p class="note">从所选公开 GitHub 仓库检查正式版本，更新由你决定。</p>
      <label class="field"><span>GitHub 仓库</span><input class="input" id="update-repository" value="${esc(state.settings.repository)}" placeholder="用户名/仓库名" ${disabled ? 'disabled' : ''}></label>
      <button class="button secondary" data-update-save ${disabled ? 'disabled' : ''}>保存更新来源</button></details></div>`;
  }

  async save(enabled) {
    const repo = document.querySelector('#update-repository')?.value || this.state.settings.repository;
    await this.api('updates/settings', {enabled, repository: repo});
    await this.poll();
  }

  async poll() {
    clearTimeout(this.timer);
    try {
      this.state = await this.api('updates/status', {});
      if (this.active) this.render();
      const key = this.state.repository + '/' + this.state.version;
      if (this.state.prompt && this.shown !== key) {
        if (document.querySelector('dialog[open]')) {
          this.timer = setTimeout(() => this.poll(), 2000);
          return;
        }
        this.shown = key;
        this.prompt();
      }
      if (busy.has(this.state.phase)) this.timer = setTimeout(() => this.poll(), 1200);
    } catch { /* An offline check never interrupts editing. */ }
  }

  prompt() {
    const state = this.state, dialog = document.createElement('dialog');
    dialog.className = 'player-action-dialog';
    dialog.innerHTML = `<h2>有可选的软件更新</h2><p>${esc(state.current)} → ${esc(state.version)}</p>
      <pre class="update-release-notes">${esc(state.notes || '请查看 GitHub 发布说明。')}</pre>
      <div class="player-action-choices"><button class="button primary" data-update-download>${state.can_install ? '下载更新' : '查看新版下载'}</button>
      <button class="button secondary" data-update-later>稍后再说</button></div>`;
    dialog.querySelector('[data-update-download]').onclick = async () => {
      dialog.close();
      try {
        await this.api(state.can_install ? 'updates/download' : 'updates/open', {});
        await this.poll();
      } catch (error) { this.toast(error.message, true); }
    };
    dialog.querySelector('[data-update-later]').onclick = () => dialog.close();
    dialog.onclose = () => { dialog.remove(); this.api('updates/dismiss', {}).catch(() => {}); };
    document.body.append(dialog);
    dialog.showModal();
  }

  async install() {
    if (!await this.confirmUpdate()) return;
    await this.prepareUpdate();
    const title = document.title;
    const windowTitle = 'StudioUpdateClose-' + crypto.randomUUID().replaceAll('-', '');
    document.title = windowTitle;
    try {
      await new Promise(resolve => setTimeout(resolve, 250));
      await this.api('updates/install', {window_title: windowTitle});
      await this.poll();
    } catch (error) {
      document.title = title;
      throw error;
    }
  }

  async click(event) {
    const button = event.target.closest('[data-update-check],[data-update-save],[data-update-open],[data-update-start],[data-update-install]');
    if (!button || button.disabled) return;
    button.disabled = true;
    try {
      if (button.hasAttribute('data-update-install')) { await this.install(); return; }
      if (button.hasAttribute('data-update-save')) {
        await this.save(document.querySelector('#update-auto').checked);
        this.toast('更新来源已保存');
        return;
      }
      const action = button.hasAttribute('data-update-open') ? 'open' : button.hasAttribute('data-update-start') ? 'download' : 'check';
      await this.api('updates/' + action, {});
      await this.poll();
    } catch (error) { this.toast(error.message, true); }
    finally { button.disabled = false; }
  }
}
