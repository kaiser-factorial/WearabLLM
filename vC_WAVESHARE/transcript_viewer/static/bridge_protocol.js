(function attachBridgeProtocol(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.WearabLLMBridgeProtocol = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function bridgeProtocolFactory() {
  function errorMessage(payload, fallback = "Bridge request failed.") {
    if (payload && typeof payload === "object" && payload.error && typeof payload.error === "object") {
      return String(payload.error.message || fallback);
    }
    return fallback;
  }

  function unwrapV2(payload) {
    if (!payload || typeof payload !== "object" || payload.ok !== true) {
      throw new Error(errorMessage(payload));
    }
    if (!payload.data || typeof payload.data !== "object" || Array.isArray(payload.data)) {
      throw new Error("Bridge v2 success data must be a JSON object.");
    }
    return { ok: true, ...payload.data };
  }

  return { errorMessage, unwrapV2 };
});
