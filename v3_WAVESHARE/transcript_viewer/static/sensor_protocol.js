(function installSensorProtocol(root, factory) {
  const protocol = factory();
  if (typeof module === "object" && module.exports) module.exports = protocol;
  root.WearabLLMSensorProtocol = protocol;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  const PACKET_SIZE = 12;
  const PACKET_VERSION = 1;

  function asDataView(packet) {
    if (packet instanceof DataView) return packet;
    if (packet instanceof ArrayBuffer) return new DataView(packet);
    if (ArrayBuffer.isView(packet)) {
      return new DataView(packet.buffer, packet.byteOffset, packet.byteLength);
    }
    return null;
  }

  function decode(packet) {
    const view = asDataView(packet);
    if (!view || view.byteLength < PACKET_SIZE) {
      return { kind: "packet-error" };
    }

    const version = view.getUint8(0);
    if (version !== PACKET_VERSION) {
      return { kind: "version-error", version };
    }

    const flags = view.getUint8(1);
    const sequence = view.getUint16(2, true);
    const rawAdc = view.getUint16(6, true);
    const uptimeMs = view.getUint32(8, true);
    if (sequence === 0 && (flags & 0x01) === 0) {
      return { kind: "waiting" };
    }
    if ((flags & 0x01) === 0) {
      return { kind: "sensor-error", sequence, rawAdc, uptimeMs };
    }

    const celsius = view.getInt16(4, true) / 100;
    return {
      kind: "reading",
      sequence,
      celsius,
      fahrenheit: (celsius * 9) / 5 + 32,
      rawAdc,
      uptimeMs,
    };
  }

  return Object.freeze({ PACKET_SIZE, PACKET_VERSION, decode });
});
