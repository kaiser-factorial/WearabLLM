const assert = require("node:assert/strict");
const fixtures = require("../../protocol/v2/fixtures.json");
const schema = require("../../protocol/v2/envelope.schema.json");
const protocol = require("../static/bridge_protocol.js");

const success = protocol.unwrapV2(fixtures.success);
assert.equal(success.ok, true);
assert.equal(success.command, "GP");
assert.equal(success.reply, "Protocol v2 is ready.");
assert.equal(protocol.errorMessage(fixtures.error), "Missing transcript");
assert.throws(() => protocol.unwrapV2(fixtures.error), /Missing transcript/);
assert.equal(schema.oneOf.length, 2);

console.log("bridge protocol: shared v2 success/error fixtures passed");
