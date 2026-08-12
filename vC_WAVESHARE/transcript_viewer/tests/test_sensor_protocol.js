const assert = require("node:assert/strict");
const protocol = require("../static/sensor_protocol.js");

function packet({ version = 1, flags = 1, sequence = 1, centiC = 0, rawAdc = 0, uptimeMs = 0 } = {}) {
  const bytes = Buffer.alloc(protocol.PACKET_SIZE);
  bytes.writeUInt8(version, 0);
  bytes.writeUInt8(flags, 1);
  bytes.writeUInt16LE(sequence, 2);
  bytes.writeInt16LE(centiC, 4);
  bytes.writeUInt16LE(rawAdc, 6);
  bytes.writeUInt32LE(uptimeMs, 8);
  return bytes;
}

assert.deepEqual(protocol.decode(packet({ flags: 0, sequence: 0 })), { kind: "waiting" });
assert.deepEqual(protocol.decode(Buffer.alloc(11)), { kind: "packet-error" });
assert.deepEqual(protocol.decode(packet({ version: 2 })), { kind: "version-error", version: 2 });
assert.deepEqual(
  protocol.decode(packet({ flags: 0, sequence: 9, rawAdc: 4095, uptimeMs: 1000 })),
  { kind: "sensor-error", sequence: 9, rawAdc: 4095, uptimeMs: 1000 },
);
assert.deepEqual(
  protocol.decode(packet({ sequence: 42, centiC: 2345, rawAdc: 2048, uptimeMs: 123000 })),
  { kind: "reading", sequence: 42, celsius: 23.45, fahrenheit: 74.21, rawAdc: 2048, uptimeMs: 123000 },
);
assert.deepEqual(
  protocol.decode(packet({ sequence: 43, centiC: -525, rawAdc: 3000, uptimeMs: 124000 })),
  { kind: "reading", sequence: 43, celsius: -5.25, fahrenheit: 22.55, rawAdc: 3000, uptimeMs: 124000 },
);

console.log("sensor protocol: 6 route cases passed");
