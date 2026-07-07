// バッチ実行ページ(view-batch)の描画。config.jsのworkflows一覧から
// カードを生成し、各カードのボタンでworkflow_dispatchをトリガーする。
// 実行履歴はgithub-api.jsのlistWorkflowRuns(未認証fetch)で取得するため、
// 🔓解錠していなくても履歴表示自体は見られる。
(function () {
  const GH = window.MinerviniGitHub;

  let wired = false;

  function statusClassFor(run) {
    if (run.status === "completed") return `run-status-${run.conclusion || "unknown"}`;
    return `run-status-${run.status}`;
  }

  function statusLabelFor(run) {
    if (run.status === "completed") {
      const map = { success: "成功", failure: "失敗", cancelled: "キャンセル", skipped: "スキップ" };
      return map[run.conclusion] || run.conclusion || "完了";
    }
    const map = { in_progress: "実行中", queued: "待機中" };
    return map[run.status] || run.status;
  }

  function formatDate(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString("ja-JP", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  function renderCards() {
    const container = document.getElementById("batch-cards");
    if (!container) return;
    container.innerHTML = "";
    const workflows = window.MINERVINI_CONFIG.workflows || [];
    for (const wf of workflows) {
      const card = document.createElement("div");
      card.className = "batch-card";

      const info = document.createElement("div");
      info.className = "batch-card-info";
      const title = document.createElement("div");
      title.className = "batch-card-title";
      title.textContent = wf.label;
      const desc = document.createElement("div");
      desc.className = "batch-card-desc";
      desc.textContent = wf.desc;
      info.appendChild(title);
      info.appendChild(desc);

      const actions = document.createElement("div");
      actions.className = "batch-card-actions";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "secondary write-btn";
      btn.textContent = "実行";
      btn.addEventListener("click", () => {
        window.MinerviniFundamentalsUI.triggerWorkflow(btn, wf.file);
      });
      actions.appendChild(btn);

      card.appendChild(info);
      card.appendChild(actions);
      container.appendChild(card);
    }
  }

  async function renderHistory() {
    const container = document.getElementById("batch-history");
    if (!container) return;
    container.innerHTML = "";
    const workflows = window.MINERVINI_CONFIG.workflows || [];
    for (const wf of workflows) {
      const section = document.createElement("div");
      const h3 = document.createElement("h3");
      h3.textContent = wf.label;
      section.appendChild(h3);

      let runs = [];
      try {
        runs = await GH.listWorkflowRuns(wf.file, 5);
      } catch (e) {
        const p = document.createElement("p");
        p.className = "tier-note";
        p.textContent = `実行履歴の取得に失敗しました: ${e.message || e}`;
        section.appendChild(p);
        container.appendChild(section);
        continue;
      }

      if (!runs.length) {
        const p = document.createElement("p");
        p.className = "tier-note";
        p.textContent = "実行履歴なし";
        section.appendChild(p);
        container.appendChild(section);
        continue;
      }

      const table = document.createElement("table");
      table.className = "run-history-table";
      const thead = document.createElement("thead");
      thead.innerHTML = "<tr><th>状態</th><th>日時</th><th>#</th></tr>";
      table.appendChild(thead);
      const tbody = document.createElement("tbody");
      for (const run of runs) {
        const tr = document.createElement("tr");
        const tdStatus = document.createElement("td");
        const badge = document.createElement("span");
        badge.className = `run-status-badge ${statusClassFor(run)}`;
        badge.textContent = statusLabelFor(run);
        tdStatus.appendChild(badge);
        const tdDate = document.createElement("td");
        tdDate.textContent = formatDate(run.created_at);
        const tdNum = document.createElement("td");
        const link = document.createElement("a");
        link.href = run.html_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = `#${run.run_number}`;
        tdNum.appendChild(link);
        tr.appendChild(tdStatus);
        tr.appendChild(tdDate);
        tr.appendChild(tdNum);
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      section.appendChild(table);
      container.appendChild(section);
    }
  }

  // SPAルーターから毎回呼ばれる想定。カード自体は静的なのでwiredガードで
  // 再生成を避け、履歴だけ毎回再取得して最新化する。
  function initBatchView() {
    if (!wired) {
      renderCards();
      wired = true;
    }
    renderHistory();
  }

  window.MinerviniBatch = { initBatchView };
})();
