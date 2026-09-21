"use strict";
// Node-runnable, dependency-free test for the web formatting helpers.
//   node web/test/format.test.js
const assert = require("assert");
const { formatCount, shortUrl } = require("../src/util/format.js");

assert.strictEqual(formatCount(0), "0");
assert.strictEqual(formatCount(1234567), "1,234,567");
assert.strictEqual(shortUrl("http://nmb.us/", "27"), "http://nmb.us/27");
assert.strictEqual(shortUrl("http://nmb.us", "27"), "http://nmb.us/27");
assert.throws(() => formatCount("x"), TypeError);

console.log("web/format: all assertions passed");
