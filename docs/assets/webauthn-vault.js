// Passkey (WebAuthn PRF) + vault-in-repo authentication.
//
// Target environment: iOS 18+ Safari only, always used in Private Browsing.
// Because private browsing gives no persistent storage, this module never
// writes to localStorage/sessionStorage/IndexedDB. The only persistence is
// docs/auth/vault.json itself, committed to the repo and served statically
// by Pages -- readable by anyone (it's a public repo), but the PAT inside it
// is AES-GCM encrypted with a key that only the enrolled passkey's PRF
// output can derive. The decrypted PAT lives in github-api.js's in-memory
// token variable for the tab's lifetime only.
(function () {
  const VAULT_RELATIVE_PATH = "auth/vault.json"; // same-origin, unauthenticated fetch
  const HKDF_INFO = "pat-vault-v1";

  function bufToB64url(buf) {
    const bytes = new Uint8Array(buf);
    let bin = "";
    bytes.forEach((b) => (bin += String.fromCharCode(b)));
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function b64urlToBuf(b64url) {
    let b64 = b64url.replace(/-/g, "+").replace(/_/g, "/");
    while (b64.length % 4) b64 += "=";
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes.buffer;
  }

  function isSupported() {
    return !!(window.PublicKeyCredential && window.crypto && window.crypto.subtle && navigator.credentials);
  }

  // -----------------------------------------------------------------------
  // vault.json read (plain static fetch -- no GitHub auth needed to read it)
  // -----------------------------------------------------------------------

  async function fetchVault() {
    let resp;
    try {
      resp = await fetch(VAULT_RELATIVE_PATH, { cache: "no-store" });
    } catch (e) {
      throw new Error("vault.jsonの取得に失敗しました(ネットワークエラー)");
    }
    if (resp.status === 404) return null;
    if (!resp.ok) throw new Error(`vault.jsonの取得に失敗しました (${resp.status})`);
    return resp.json();
  }

  // -----------------------------------------------------------------------
  // WebAuthn PRF
  // -----------------------------------------------------------------------

  async function createPasskey() {
    const challenge = crypto.getRandomValues(new Uint8Array(32));
    const userId = crypto.getRandomValues(new Uint8Array(16));
    const cred = await navigator.credentials.create({
      publicKey: {
        challenge,
        rp: { name: "Minervini Screener", id: window.location.hostname },
        user: { id: userId, name: "minervini-dashboard", displayName: "Minervini Dashboard" },
        pubKeyCredParams: [
          { type: "public-key", alg: -7 }, // ES256
          { type: "public-key", alg: -257 }, // RS256
        ],
        authenticatorSelection: { residentKey: "required", userVerification: "required" },
        extensions: { prf: {} },
        timeout: 60000,
      },
    });
    const ext = cred.getClientExtensionResults();
    if (!ext.prf || !ext.prf.enabled) {
      throw new Error("この環境はPRF未対応です(iOS 18以降のSafariが必要)");
    }
    return new Uint8Array(cred.rawId).buffer;
  }

  async function evalPrf(credentialIdBuf, saltBuf) {
    const assertion = await navigator.credentials.get({
      publicKey: {
        challenge: crypto.getRandomValues(new Uint8Array(32)),
        allowCredentials: [{ type: "public-key", id: credentialIdBuf }],
        userVerification: "required",
        extensions: { prf: { eval: { first: saltBuf } } },
        timeout: 60000,
      },
    });
    const results = assertion.getClientExtensionResults();
    const first = results.prf && results.prf.results && results.prf.results.first;
    if (!first) {
      throw new Error("パスキーからのPRF出力を取得できませんでした。");
    }
    return first; // ArrayBuffer
  }

  // -----------------------------------------------------------------------
  // HKDF-SHA256 -> AES-GCM 256
  // -----------------------------------------------------------------------

  async function deriveAesKey(prfOutput, hkdfSaltBuf) {
    const baseKey = await crypto.subtle.importKey("raw", prfOutput, "HKDF", false, ["deriveKey"]);
    return crypto.subtle.deriveKey(
      { name: "HKDF", hash: "SHA-256", salt: hkdfSaltBuf, info: new TextEncoder().encode(HKDF_INFO) },
      baseKey,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"]
    );
  }

  // -----------------------------------------------------------------------
  // Setup / rotation: enter a PAT, enroll (or re-enroll) a passkey, encrypt,
  // commit vault.json via the Contents API using the just-entered PAT.
  // -----------------------------------------------------------------------

  async function setupVault(plainPat) {
    if (!isSupported()) {
      throw new Error("この環境はWebAuthnに対応していません(iOS 18以降のSafariが必要)");
    }

    const credentialId = await createPasskey();
    const prfSalt = crypto.getRandomValues(new Uint8Array(32));
    const hkdfSalt = crypto.getRandomValues(new Uint8Array(32));
    const iv = crypto.getRandomValues(new Uint8Array(12));

    const prfOutput = await evalPrf(credentialId, prfSalt.buffer);
    const key = await deriveAesKey(prfOutput, hkdfSalt.buffer);
    const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(plainPat));

    const vault = {
      version: 1,
      credentialId: bufToB64url(credentialId),
      prfSalt: bufToB64url(prfSalt.buffer),
      hkdfSalt: bufToB64url(hkdfSalt.buffer),
      iv: bufToB64url(iv.buffer),
      ciphertext: bufToB64url(ciphertext),
      createdAt: new Date().toISOString(),
    };

    const GH = window.MinerviniGitHub;
    GH.setToken(plainPat); // needed to commit; also leaves the session unlocked afterward
    const existing = await GH.getRepoFile(window.MINERVINI_CONFIG.vaultPath);
    const resp = await GH.putRepoFile(
      window.MINERVINI_CONFIG.vaultPath,
      JSON.stringify(vault, null, 2) + "\n",
      existing.sha,
      "auth: rotate dashboard vault"
    );
    if (!resp.ok) {
      GH.setToken(""); // setup failed -- don't leave a token from an uncommitted vault
      throw await GH.toApiError(resp);
    }
    return vault;
  }

  // -----------------------------------------------------------------------
  // Unlock: PRF eval against the stored salts -> derive key -> decrypt.
  // -----------------------------------------------------------------------

  async function unlock(vault) {
    if (!isSupported()) {
      throw new Error("この環境はWebAuthnに対応していません(iOS 18以降のSafariが必要)");
    }
    const credentialId = b64urlToBuf(vault.credentialId);
    const prfSaltBuf = b64urlToBuf(vault.prfSalt);
    const hkdfSaltBuf = b64urlToBuf(vault.hkdfSalt);
    const ivBuf = b64urlToBuf(vault.iv);
    const ciphertextBuf = b64urlToBuf(vault.ciphertext);

    const prfOutput = await evalPrf(credentialId, prfSaltBuf);
    const key = await deriveAesKey(prfOutput, hkdfSaltBuf);
    const plainBuf = await crypto.subtle.decrypt({ name: "AES-GCM", iv: new Uint8Array(ivBuf) }, key, ciphertextBuf);
    const pat = new TextDecoder().decode(plainBuf);
    window.MinerviniGitHub.setToken(pat);
  }

  function lock() {
    window.MinerviniGitHub.setToken("");
  }

  window.MinerviniVault = {
    isSupported,
    fetchVault,
    setupVault,
    unlock,
    lock,
  };
})();
