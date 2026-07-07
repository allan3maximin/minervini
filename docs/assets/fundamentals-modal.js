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

  // Body scroll lock while a modal is open. `overflow: hidden` alone is not
  // enough on iOS Safari (the page behind still rubber-bands, which is the
  // "modal scroll jitters" bug), so pin the body with position:fixed and
  // restore the scroll offset on close.
  let savedScrollY = 0;

  function lockBodyScroll() {
    savedScrollY = window.scrollY || 0;
    document.body.style.top = `-${savedScrollY}px`;
    document.body.classList.add("modal-open");
  }

  function unlockBodyScroll() {
    document.body.classList.remove("modal-open");
    document.body.style.top = "";
    window.scrollTo(0, savedScrollY);
  }

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
    lockBodyScroll();
  }

  function closeModal() {
    const existing = document.getElementById("minervini-modal-overlay");
    if (existing) {
      existing.remove();
      unlockBodyScroll();
    }
  }

  // -----------------------------------------------------------------------
  // Settings modal: passkey vault setup / rotation.
  //
  // Same modal handles both first-time setup and re-enrollment (rotation)
  // -- setupVault() always creates a fresh passkey + vault and overwrites
  // whatever vault.json currently holds, so a stale/lost passkey is
  // recoverable by just entering a new PAT here.
  // -----------------------------------------------------------------------

  function openVaultSetupModal(options) {
    options = options || {};
    const isRotation = !!options.isRotation;
    const el = document.createElement("div");
    el.className = "modal-content";
    el.innerHTML = `
      <h2>${isRotation ? "パスキーの再セットアップ / ローテーション" : "初回セットアップ (パスキー)"}</h2>
      <p class="modal-note">
        入力したPATは、この端末のFace ID/Touch IDで保護されたパスキーで暗号化し、
        <code>docs/auth/vault.json</code> としてリポジトリにコミットします。
        このリポジトリは公開設定のため <strong>vault.json自体は誰でも閲覧できます</strong>
        (中身は暗号化済みですが)。PATは必ず
        <strong>Fine-grained・対象リポジトリ限定・最小権限</strong>で発行してください。<br><br>
        対応環境: <strong>iOS 18以降のSafari</strong> のみです。復号鍵やPATはどのストレージにも
        保存されず、このタブを閉じると消えます。
      </p>
      <label class="field-label">Personal Access Token</label>
      <input type="password" id="vault-pat-input" class="modal-input" autocomplete="off" placeholder="github_pat_..." />
      <div id="vault-setup-error" class="form-error" hidden></div>
      <div class="modal-actions">
        <button type="button" id="vault-setup-cancel">キャンセル</button>
        <button type="button" id="vault-setup-submit" class="primary">パスキーを作成してコミット</button>
      </div>
    `;
    openModal(el);
    document.getElementById("vault-setup-cancel").addEventListener("click", closeModal);
    document.getElementById("vault-setup-submit").addEventListener("click", onSubmit);

    async function onSubmit() {
      const errEl = document.getElementById("vault-setup-error");
      errEl.hidden = true;
      const patInput = document.getElementById("vault-pat-input");
      let pat = patInput.value.trim();
      if (!pat) {
        errEl.textContent = "PATを入力してください。";
        errEl.hidden = false;
        return;
      }
      const submitBtn = document.getElementById("vault-setup-submit");
      submitBtn.disabled = true;
      submitBtn.textContent = "パスキー作成中...(Face ID/Touch IDの確認が表示されます)";
      try {
        await window.MinerviniVault.setupVault(pat);
        pat = ""; // drop the local reference to the plaintext PAT
        patInput.value = "";
        closeModal();
        alert(
          "セットアップが完了し、vault.jsonをコミットしました。解錠済みなのでこのまま操作できます。\n" +
            "他の端末/タブでは反映まで数分かかる場合があります(Pagesのキャッシュ)。"
        );
        if (window.MinerviniFundamentalsUI.onSaved) window.MinerviniFundamentalsUI.onSaved();
      } catch (e) {
        errEl.textContent = e.message || String(e);
        errEl.hidden = false;
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = "パスキーを作成してコミット";
      }
    }
  }

  // -----------------------------------------------------------------------
  // Fundamentals input/edit modal
  // -----------------------------------------------------------------------

  async function openFundamentalsModal(code, name) {
    // Two persistence modes: with a PAT the form commits via the Contents
    // API; without one ("no git key" mode) it validates, copies ready-made
    // CSV rows to the clipboard, and links to GitHub's web editor so the
    // user can paste + commit manually. Prefill works in both modes (the
    // repo is public, so the CSV is readable without auth).
    const manualMode = !GH.hasToken();
    const saveLabel = manualMode ? "CSV行をコピー(手動コミット)" : "保存してコミット";

    const el = document.createElement("div");
    el.className = "modal-content";
    el.innerHTML = `
      <h2>ファンダ入力 / 編集</h2>
      <div class="modal-note">コード: <strong>${escapeHtml(code)}</strong> ${escapeHtml(name || "")}</div>
      ${manualMode ? '<div class="modal-note">現在は手動コミットモードです。保存を押すとCSV行をクリップボードにコピーするので、GitHubの編集画面で manual/fundamentals.csv に貼り付けてコミットしてください。</div>' : ""}
      <div id="fund-form-body">読み込み中...</div>
      <div id="fund-form-error" class="form-error" hidden></div>
      <div id="fund-manual-result" class="modal-note" hidden></div>
      <div class="modal-actions">
        <button type="button" id="fund-cancel">キャンセル</button>
        <button type="button" id="fund-save" class="primary" disabled>${saveLabel}</button>
      </div>
    `;
    openModal(el);
    document.getElementById("fund-cancel").addEventListener("click", closeModal);

    const labels = generateQuarterLabels(QUARTER_COUNT);
    let existingByQuarter = {};
    let latestExisting = null;
    try {
      const existingRows = manualMode
        ? (await GH.fetchFundamentalsCsvPublic()).filter((r) => r.code === code)
        : await GH.getExistingRowsForCode(code);
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

    // Batch (J-Quants)-fetched baseline: docs/data/fundamentals_public.json
    // is written by the daily pipeline (src/data/fundamentals.py
    // write_public_json). It's only used to fill quarters that have no
    // manual/fundamentals.csv row yet, so a manual entry always wins.
    // Fetch failures here are silent -- the form still works manual-only.
    let autoByQuarter = {};
    try {
      const resp = await fetch("data/fundamentals_public.json", { cache: "no-store" });
      if (resp.ok) {
        const all = await resp.json();
        const entry = all[code];
        if (entry && entry.quarters) {
          entry.quarters.forEach((q) => {
            autoByQuarter[q.fiscal_quarter] = q;
          });
        }
      }
    } catch (e) {
      /* non-fatal: baseline prefill only */
    }

    renderForm(labels, existingByQuarter, latestExisting, autoByQuarter);
    document.getElementById("fund-save").disabled = false;
    document.getElementById("fund-save").addEventListener("click", () => onSaveClicked(code));

    function pickValue(existingVal, autoVal) {
      if (existingVal !== undefined && existingVal !== null && existingVal !== "") return existingVal;
      if (autoVal !== undefined && autoVal !== null) return autoVal;
      return "";
    }

    function renderForm(labels, existingByQuarter, latestExisting, autoByQuarter) {
      const body = document.getElementById("fund-form-body");
      const rowsHtml = labels
        .map((label, i) => {
          const existing = existingByQuarter[label] || {};
          const auto = (autoByQuarter && autoByQuarter[label]) || {};
          const epsVal = pickValue(existing.eps, auto.eps);
          const revVal = pickValue(existing.revenue, auto.revenue);
          const isAutoFilled =
            (existing.eps === undefined || existing.eps === null || existing.eps === "") &&
            (existing.revenue === undefined || existing.revenue === null || existing.revenue === "") &&
            (auto.eps != null || auto.revenue != null);
          return `
            <tr${isAutoFilled ? ' class="auto-prefill-row" title="J-Quants自動取得値(未確定)。保存すると手動値として確定します。"' : ""}>
              <td><input type="text" class="q-fiscal" data-i="${i}" value="${escapeAttr(label)}"></td>
              <td><input type="number" step="any" class="q-eps" data-i="${i}" value="${escapeAttr(epsVal)}"></td>
              <td><input type="number" step="any" class="q-revenue" data-i="${i}" value="${escapeAttr(revVal)}"></td>
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
      const originalLabel = saveBtn.textContent;
      saveBtn.disabled = true;
      saveBtn.textContent = "処理中...";

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
          }))
          // drop quarters with no actual data so the CSV doesn't accumulate
          // empty placeholder rows
          .filter((r) => r.eps !== "" || r.revenue !== "" || r.monthly_yoy !== "");

        if (newRows.length === 0) {
          renderFormError("EPSまたは売上高をどこかの四半期に入力してください。");
          return;
        }

        if (manualMode) {
          const csvLines = newRows
            .map((r) => [r.code, r.fiscal_quarter, r.eps, r.revenue, r.monthly_yoy, r.checked_date].join(","))
            .join("\n");
          await navigator.clipboard.writeText(csvLines);
          const { owner, repo, branch, fundamentalsPath } = window.MINERVINI_CONFIG;
          const editUrl = `https://github.com/${owner}/${repo}/edit/${branch}/${fundamentalsPath}`;
          const resultEl = document.getElementById("fund-manual-result");
          resultEl.innerHTML =
            `${newRows.length}行をクリップボードにコピーしました。` +
            `<a href="${editUrl}" target="_blank" rel="noopener">GitHubで fundamentals.csv を編集</a>` +
            `を開き、この銘柄(${escapeHtml(code)})の既存行を置き換える形で貼り付けてコミットしてください。`;
          resultEl.hidden = false;
        } else {
          await GH.saveFundamentalsRows(code, newRows);
          markPending(code);
          closeModal();
          alert(`${code} のファンダデータを manual/fundamentals.csv に保存しました。次回パイプライン実行で反映されます。`);
          if (window.MinerviniFundamentalsUI && window.MinerviniFundamentalsUI.onSaved) {
            window.MinerviniFundamentalsUI.onSaved();
          }
        }
      } catch (e) {
        renderFormError(e.message || String(e));
      } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = originalLabel;
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

  // 汎用版: バッチ実行ページの各カードから任意のワークフローをトリガーする。
  async function triggerWorkflow(buttonEl, workflowFile) {
    buttonEl.disabled = true;
    const original = buttonEl.textContent;
    buttonEl.textContent = "実行をリクエスト中...";
    try {
      await GH.dispatchWorkflow(workflowFile);
      alert("実行をリクエストしました。GitHub Actionsの実行状況をご確認ください。");
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
        alert(`実行のリクエストに失敗しました: ${e.message || e}`);
      }
    }
  }

  window.MinerviniFundamentalsUI = {
    openVaultSetupModal,
    openFundamentalsModal,
    closeModal,
    reconcilePending,
    isPending,
    triggerManualRerun,
    triggerWorkflow,
    generateQuarterLabels,
    validateForm,
    onSaved: null, // app.js sets this to re-render the dashboard after a save
  };
})();
