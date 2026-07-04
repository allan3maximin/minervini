// Settings modal (PAT storage) + fundamentals input/edit modal, backed by
// github-api.js. Exposes window.MinerviniFundamentalsUI for app.js to call.
(function () {
  const GH = window.MinerviniGitHub;
  const PENDING_KEY = "minervini_pending_fund";
  const QUARTER_COUNT = 8;

  // -----------------------------------------------------------------------
  // Quarter label generation: the most recently *completed* quarter is last
  // quarter (the current one isn't finished yet), then 7 more counting back.
  // -----------------------------------------------------------------------

  function currentQuarter(date) {
    date = date || new Date();
    return { year: date.getFullYear(), q: Math.floor(date.getMonth() / 3) + 1 };
  }

  function shiftQuarter({ year, q }, n) {
    const total = year * 4 + (q - 1) - n;
    const newYear = Math.floor(total / 4);
    const newQ = ((total % 4) + 4) % 4 + 1;
    return { year: newYear, q: newQ };
  }

  function generateQuarterLabels(count) {
    count = count || QUARTER_COUNT;
    const latestReportable = shiftQuarter(currentQuarter(), 1);
    const labels = [];
    for (let i = 0; i < count; i++) {
      const q = shiftQuarter(latestReportable, i);
      labels.push(`${q.year}Q${q.q}`); // index 0 = most recent
    }
    return labels;
  }

  // -----------------------------------------------------------------------
  // Validation (mirrors src/data/fundamentals.py's CSV rules)
  // -----------------------------------------------------------------------

  const QUARTER_RE = /^\d{4}Q[1-4]$/;

  function isValidNumberField(value) {
    if (value === "" || value == null) return true;
    return /^-?\d+(\.\d+)?$/.test(String(value).trim());
  }

  function isValidDateField(value) {
    if (!value) return true;
    return /^\d{4}-\d{2}-\d{2}$/.test(value);
  }

  // rows: [{fiscal_quarter, eps, revenue}], monthlyYoy: string, checkedDate: string
  function validateForm(rows, monthlyYoy, checkedDate) {
    const errors = {}; // key: `row-{i}-{field}` or "monthly_yoy" / "checked_date"
    const seen = new Set();

    rows.forEach((row, i) => {
      const fq = (row.fiscal_quarter || "").trim();
      if (!QUARTER_RE.test(fq)) {
        errors[`row-${i}-fiscal_quarter`] = "YYYYQn形式で入力してください(例: 2026Q2)";
      } else if (seen.has(fq)) {
        errors[`row-${i}-fiscal_quarter`] = "この四半期は重複しています";
      } else {
        seen.add(fq);
      }
      if (!isValidNumberField(row.eps)) {
        errors[`row-${i}-eps`] = "数値で入力してください";
      }
      if (!isValidNumberField(row.revenue)) {
        errors[`row-${i}-revenue`] = "数値で入力してください";
      }
    });

    if (!isValidNumberField(monthlyYoy)) {
      errors["monthly_yoy"] = "数値で入力してください";
    }
    if (!isValidDateField(checkedDate)) {
      errors["checked_date"] = "YYYY-MM-DD形式で入力してください";
    }

    return errors;
  }

  // -----------------------------------------------------------------------
  // Pending-fund badge bookkeeping (localStorage)
  // -----------------------------------------------------------------------

  function loadPending() {
    try {
      return JSON.parse(localStorage.getItem(PENDING_KEY) || "{}");
    } catch (e) {
      return {};
    }
  }

  function savePending(pending) {
    localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  }

  function markPending(code) {
    const pending = loadPending();
    pending[code] = { committedAt: new Date().toISOString() };
    savePending(pending);
  }

  // Drops any pending entry the report has since caught up with. Call once
  // per dashboard render, before checking isPending().
  function reconcilePending(reportGeneratedAt) {
    const pending = loadPending();
    const reportTime = reportGeneratedAt ? new Date(reportGeneratedAt).getTime() : 0;
    let changed = false;
    for (const code of Object.keys(pending)) {
      if (new Date(pending[code].committedAt).getTime() < reportTime) {
        delete pending[code];
        changed = true;
      }
    }
    if (changed) savePending(pending);
    return pending;
  }

  function isPending(pending, code) {
    return !!pending[code];
  }

  // -----------------------------------------------------------------------
  // Generic modal shell
  // -----------------------------------------------------------------------

  function openModal(contentEl) {
    closeModal();
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.id = "minervini-modal-overlay";
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) closeModal();
    });
    const box = document.createElement("div");
    box.className = "modal-box";
    box.appendChild(contentEl);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
  }

  function closeModal() {
    const existing = document.getElementById("minervini-modal-overlay");
    if (existing) existing.remove();
  }

  // -----------------------------------------------------------------------
  // Settings modal (token entry)
  // -----------------------------------------------------------------------

  function openSettingsModal(onSaved) {
    const el = document.createElement("div");
    el.className = "modal-content";
    el.innerHTML = `
      <h2>設定 (GitHub Personal Access Token)</h2>
      <p class="modal-note">
        Fine-grained PATを発行し、対象リポジトリの <strong>Contents: Read and write</strong>
        権限のみを付与してください。手動再実行ボタンを使う場合は
        <strong>Actions: Read and write</strong> も追加で付与します。<br>
        トークンはこのブラウザの localStorage にのみ保存され、サーバへは送信されません。
        <strong>共有端末では保存しないでください。</strong>
      </p>
      <label class="field-label">Personal Access Token</label>
      <input type="password" id="gh-token-input" class="modal-input" autocomplete="off" placeholder="github_pat_..." />
      <div class="modal-actions">
        <button type="button" id="gh-token-clear">クリア</button>
        <button type="button" id="gh-token-cancel">キャンセル</button>
        <button type="button" id="gh-token-save" class="primary">保存</button>
      </div>
    `;
    openModal(el);
    document.getElementById("gh-token-input").value = GH.getToken();
    document.getElementById("gh-token-cancel").addEventListener("click", closeModal);
    document.getElementById("gh-token-clear").addEventListener("click", () => {
      GH.setToken("");
      document.getElementById("gh-token-input").value = "";
    });
    document.getElementById("gh-token-save").addEventListener("click", () => {
      const value = document.getElementById("gh-token-input").value.trim();
      GH.setToken(value);
      closeModal();
      if (onSaved) onSaved();
    });
  }

  // -----------------------------------------------------------------------
  // Fundamentals input/edit modal
  // -----------------------------------------------------------------------

  async function openFundamentalsModal(code, name) {
    if (!GH.hasToken()) {
      openSettingsModal(() => openFundamentalsModal(code, name));
      return;
    }

    const el = document.createElement("div");
    el.className = "modal-content";
    el.innerHTML = `
      <h2>ファンダ入力 / 編集</h2>
      <div class="modal-note">コード: <strong>${escapeHtml(code)}</strong> ${escapeHtml(name || "")}</div>
      <div id="fund-form-body">読み込み中...</div>
      <div id="fund-form-error" class="form-error" hidden></div>
      <div class="modal-actions">
        <button type="button" id="fund-cancel">キャンセル</button>
        <button type="button" id="fund-save" class="primary" disabled>保存してコミット</button>
      </div>
    `;
    openModal(el);
    document.getElementById("fund-cancel").addEventListener("click", closeModal);

    const labels = generateQuarterLabels(QUARTER_COUNT);
    let existingByQuarter = {};
    let latestExisting = null;
    try {
      const existingRows = await GH.getExistingRowsForCode(code);
      existingRows.forEach((r) => {
        existingByQuarter[r.fiscal_quarter] = r;
      });
      if (existingRows.length) {
        latestExisting = existingRows.slice().sort((a, b) =>
          a.fiscal_quarter < b.fiscal_quarter ? 1 : -1
        )[0];
      }
    } catch (e) {
      renderFormError(e.message || String(e));
    }

    renderForm(labels, existingByQuarter, latestExisting);
    document.getElementById("fund-save").disabled = false;
    document.getElementById("fund-save").addEventListener("click", () => onSaveClicked(code));

    function renderForm(labels, existingByQuarter, latestExisting) {
      const body = document.getElementById("fund-form-body");
      const rowsHtml = labels
        .map((label, i) => {
          const existing = existingByQuarter[label] || {};
          return `
            <tr>
              <td><input type="text" class="q-fiscal" data-i="${i}" value="${escapeAttr(label)}"></td>
              <td><input type="number" step="any" class="q-eps" data-i="${i}" value="${escapeAttr(existing.eps || "")}"></td>
              <td><input type="number" step="any" class="q-revenue" data-i="${i}" value="${escapeAttr(existing.revenue || "")}"></td>
            </tr>
          `;
        })
        .join("");

      body.innerHTML = `
        <table class="fund-table">
          <thead><tr><th>会計四半期</th><th>EPS</th><th>売上高</th></tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
        <label class="field-label">月次売上YoY (%、直近月次のみ)</label>
        <input type="number" step="any" id="fund-monthly-yoy" class="modal-input"
               value="${escapeAttr(latestExisting ? latestExisting.monthly_yoy || "" : "")}">
        <label class="field-label">確認日</label>
        <input type="date" id="fund-checked-date" class="modal-input"
               value="${escapeAttr(new Date().toISOString().slice(0, 10))}">
      `;
    }

    function collectFormRows() {
      const fiscalEls = el.querySelectorAll(".q-fiscal");
      const epsEls = el.querySelectorAll(".q-eps");
      const revEls = el.querySelectorAll(".q-revenue");
      const rows = [];
      for (let i = 0; i < fiscalEls.length; i++) {
        rows.push({
          fiscal_quarter: fiscalEls[i].value.trim(),
          eps: epsEls[i].value.trim(),
          revenue: revEls[i].value.trim(),
        });
      }
      return rows;
    }

    function renderFormError(message) {
      const errEl = document.getElementById("fund-form-error");
      errEl.textContent = message;
      errEl.hidden = !message;
    }

    function clearFieldErrors() {
      el.querySelectorAll(".field-error").forEach((n) => n.remove());
      el.querySelectorAll(".has-error").forEach((n) => n.classList.remove("has-error"));
    }

    function showFieldErrors(errors) {
      clearFieldErrors();
      Object.entries(errors).forEach(([key, message]) => {
        let input;
        if (key === "monthly_yoy") input = document.getElementById("fund-monthly-yoy");
        else if (key === "checked_date") input = document.getElementById("fund-checked-date");
        else {
          const m = /^row-(\d+)-(fiscal_quarter|eps|revenue)$/.exec(key);
          if (m) {
            const selector = { fiscal_quarter: ".q-fiscal", eps: ".q-eps", revenue: ".q-revenue" }[m[2]];
            input = el.querySelector(`${selector}[data-i="${m[1]}"]`);
          }
        }
        if (input) {
          input.classList.add("has-error");
          const msg = document.createElement("div");
          msg.className = "field-error";
          msg.textContent = message;
          input.insertAdjacentElement("afterend", msg);
        }
      });
    }

    async function onSaveClicked(code) {
      renderFormError("");
      const rows = collectFormRows();
      const monthlyYoy = document.getElementById("fund-monthly-yoy").value.trim();
      const checkedDate = document.getElementById("fund-checked-date").value.trim();

      const errors = validateForm(rows, monthlyYoy, checkedDate);
      if (Object.keys(errors).length > 0) {
        showFieldErrors(errors);
        return;
      }
      clearFieldErrors();

      const saveBtn = document.getElementById("fund-save");
      saveBtn.disabled = true;
      saveBtn.textContent = "保存中...";

      try {
        const newRows = rows
          .filter((r) => r.fiscal_quarter)
          .map((r, i) => ({
            code,
            fiscal_quarter: r.fiscal_quarter,
            eps: r.eps,
            revenue: r.revenue,
            monthly_yoy: i === 0 ? monthlyYoy : "",
            checked_date: i === 0 ? checkedDate : "",
          }));
        await GH.saveFundamentalsRows(code, newRows);
        markPending(code);
        closeModal();
        alert(`${code} のファンダデータを manual/fundamentals.csv に保存しました。次回パイプライン実行で反映されます。`);
        if (window.MinerviniFundamentalsUI && window.MinerviniFundamentalsUI.onSaved) {
          window.MinerviniFundamentalsUI.onSaved();
        }
      } catch (e) {
        renderFormError(e.message || String(e));
      } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = "保存してコミット";
      }
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function escapeAttr(s) {
    return escapeHtml(s);
  }

  // -----------------------------------------------------------------------
  // Manual re-run button
  // -----------------------------------------------------------------------

  async function triggerManualRerun(buttonEl) {
    buttonEl.disabled = true;
    const original = buttonEl.textContent;
    buttonEl.textContent = "実行をリクエスト中...";
    try {
      await GH.dispatchDailyWorkflow();
      alert("再スクリーニングをリクエストしました。GitHub Actionsの実行状況をご確認ください。");
      buttonEl.textContent = original;
      buttonEl.disabled = false;
    } catch (e) {
      buttonEl.textContent = original;
      if (e instanceof GH.GitHubApiError && e.status === 403) {
        buttonEl.disabled = true;
        buttonEl.title = "PATにActions: Read and write権限がありません";
        alert("手動実行には権限が不足しています。設定のPATにActions: Read and writeを追加してください。");
      } else {
        buttonEl.disabled = false;
        alert(`再スクリーニングのリクエストに失敗しました: ${e.message || e}`);
      }
    }
  }

  window.MinerviniFundamentalsUI = {
    openSettingsModal,
    openFundamentalsModal,
    closeModal,
    reconcilePending,
    isPending,
    triggerManualRerun,
    generateQuarterLabels,
    validateForm,
    onSaved: null, // app.js sets this to re-render the dashboard after a save
  };
})();
