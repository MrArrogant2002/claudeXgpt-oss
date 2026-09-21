"use strict";
// Formatting helpers shared by the web UI. Plain JS (not TS) so it runs under
// node with no build step — see ../../test/format.test.js.

/** Group a non-negative integer with thousands separators: 1234567 -> "1,234,567". */
function formatCount(n) {
  if (typeof n !== "number" || Number.isNaN(n)) {
    throw new TypeError("n must be a number");
  }
  return Math.trunc(n).toLocaleString("en-US");
}

/** Build a short-code URL from a base and a code, tolerating a trailing slash. */
function shortUrl(base, code) {
  return `${base.replace(/\/+$/, "")}/${code}`;
}

module.exports = { formatCount, shortUrl };
