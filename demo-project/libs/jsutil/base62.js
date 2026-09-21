"use strict";
// base62 encode/decode — the JavaScript mirror of
// services/api/nimbus_api/shortener.py. Kept in sync so the web/CLI can compute
// codes without a round-trip to the API.
const ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
const BASE = ALPHABET.length; // 62

function encode(n) {
  if (n < 0) throw new Error("cannot encode a negative id");
  if (n === 0) return "0";
  let out = "";
  while (n > 0) {
    out = ALPHABET[n % BASE] + out;
    n = Math.floor(n / BASE);
  }
  return out;
}

function decode(code) {
  let n = 0;
  for (const ch of code) {
    const idx = ALPHABET.indexOf(ch);
    if (idx < 0) throw new Error(`invalid base62 character: ${ch}`);
    n = n * BASE + idx;
  }
  return n;
}

module.exports = { encode, decode };
