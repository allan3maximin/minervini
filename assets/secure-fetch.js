// docs/data/*.json の取得+復号の共通層。
//
// パイプラインは DASHBOARD_DATA_KEY があると各JSONを AES-256-GCM の封筒
// {"__enc__":"aesgcm-v1","iv":<b64>,"ct":<b64>} で書き出す (src/report/secure_io.py)。
// このモジュールは fetch 結果が封筒ならセッション中のデータ鍵で復号して返し、
// 平文ならそのまま返す(移行期間・ローカル開発の両対応)。
//
// データ鍵はWebAuthn PRFパスキー保管庫 (webauthn-vault.js) の解錠時に
// setDataKey() で渡される。鍵はメモリのみ(タブを閉じると消える)。
(function () {
  let aesKey = null; // CryptoKey (session only)
  let rawKeyB64 = null;

  // リロード時のパスキー再認証回避のため、読み取り用データ鍵(base64)を
  // sessionStorage に保持する。sessionStorage はタブ(iOS Safariのプライベート
  // ブラウズ含む)を閉じると消え、同じタブ内のリロードでは残るため、
  // 「リロードは素通し・タブを閉じたら再認証」という挙動になる。
  // 書き込み用PAT(より機微)は保持しないので、書き込みはリロード後に再解錠が必要。
  // トレードオフ: XSSがあればこの鍵(暗号化JSONの復号権)が読める(個人用途で許容)。
  const SESSION_KEY = "minervini-dk";

  function b64ToBuf(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes.buffer;
  }

  function isEnvelope(obj) {
    return !!obj && typeof obj === "object" && obj.__enc__ === "aesgcm-v1";
  }

  async function setDataKey(b64, options) {
    const clean = String(b64 || "").trim();
    const buf = b64ToBuf(clean);
    if (buf.byteLength !== 32) {
      throw new Error(`データ鍵はbase64で32バイトである必要があります (実際: ${buf.byteLength}バイト)`);
    }
    aesKey = await crypto.subtle.importKey("raw", buf, { name: "AES-GCM" }, false, ["decrypt"]);
    rawKeyB64 = null; // 生鍵の文字列参照は持ち続けない
    // options.persist === false の時は保持しない(セッション復元経路での二重保存回避)。
    if (!options || options.persist !== false) {
      try {
        sessionStorage.setItem(SESSION_KEY, clean);
      } catch (e) {
        /* プライベートブラウズ等で使えない場合は黙って諦める(メモリのみ動作) */
      }
    }
    // 起動時ロック画面(app.jsのensureDataAccess)へ「鍵が入った」ことを通知する。
    window.dispatchEvent(new Event("minervini-unlocked"));
  }

  // リロード時にsessionStorageの鍵で解錠を試みる(パスキー不要)。成功すればtrue。
  async function restoreDataKey() {
    let stored = null;
    try {
      stored = sessionStorage.getItem(SESSION_KEY);
    } catch (e) {
      return false;
    }
    if (!stored) return false;
    try {
      await setDataKey(stored, { persist: false });
      return true;
    } catch (e) {
      try { sessionStorage.removeItem(SESSION_KEY); } catch (_) {}
      return false;
    }
  }

  function hasDataKey() {
    return aesKey !== null;
  }

  function clearDataKey() {
    aesKey = null;
    rawKeyB64 = null;
    try { sessionStorage.removeItem(SESSION_KEY); } catch (e) {}
  }

  async function decryptEnvelope(envelope) {
    if (!aesKey) {
      const err = new Error("データが暗号化されています。パスキーで解錠してください。");
      err.code = "LOCKED";
      throw err;
    }
    const plainBuf = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: new Uint8Array(b64ToBuf(envelope.iv)) },
      aesKey,
      b64ToBuf(envelope.ct)
    );
    return JSON.parse(new TextDecoder().decode(plainBuf));
  }

  // fetch("data/x.json").then(r => r.json()) の置き換え。
  // options.optional: true なら 404/ネットワークエラー時に null (従来のcatch(() => null)相当)。
  async function fetchJson(path, options) {
    const optional = !!(options && options.optional);
    let resp;
    try {
      resp = await fetch(path, { cache: "no-store" });
    } catch (e) {
      if (optional) return null;
      throw e;
    }
    if (!resp.ok) {
      if (optional) return null;
      throw new Error(`${path} の取得に失敗しました (${resp.status})`);
    }
    const obj = await resp.json();
    if (!isEnvelope(obj)) return obj;
    return decryptEnvelope(obj);
  }

  window.MinerviniData = { fetchJson, isEnvelope, setDataKey, restoreDataKey, hasDataKey, clearDataKey };
})();
