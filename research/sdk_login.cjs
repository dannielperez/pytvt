// ┌──────────────────────────────────────────────────────────────┐
// │  RESEARCH / REFERENCE ONLY — not part of the pytvt runtime  │
// └──────────────────────────────────────────────────────────────┘
//
// SDK login capture helper — logs into an NVR via the native SDK FFI so that
// capture_sdk.sh can record the binary handshake in a pcap. Used during
// protocol reverse engineering to compare SDK-generated packets against the
// pure-Python implementation in src/pytvt/protocol.py.
//
// See research/README.md for context.

// Uses env vars from /app/.env or environment
const { readFileSync } = require("fs");

// Load .env from Docker image if present
try {
  for (const line of readFileSync("/app/.env", "utf-8").split("\n")) {
    const m = line.match(/^([A-Z_]+)=(.+)$/);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].trim();
  }
} catch {}

const host = process.env.TVT_HOST || "192.168.1.100";
const port = parseInt(process.env.TVT_PORT || "6036", 10);
const user = process.env.TVT_USERNAME || "admin";
const pass = process.env.TVT_PASSWORD;
if (!pass) { console.error("TVT_PASSWORD not set (env or /app/.env)"); process.exit(1); }

const koffi = require("koffi");
const lib = koffi.load("/app/tvt/bin/linux/libdvrnetsdk.so");
const NET_SDK_Init = lib.func("NET_SDK_Init", "int", []);
const NET_SDK_Cleanup = lib.func("NET_SDK_Cleanup", "int", []);
const NET_SDK_Login = lib.func("NET_SDK_Login", "int", ["string", "int", "string", "string", koffi.out(koffi.pointer("uint8_t"))]);
NET_SDK_Init();
const buf = Buffer.alloc(256);
console.log(`Attempting login to ${host}:${port} as ${user}...`);
const h = NET_SDK_Login(host, port, user, pass, buf);
console.log("Handle:", h);
if (h > 0) {
  lib.func("NET_SDK_Logout", "int", ["int"])(h);
  console.log("Logged out");
}
NET_SDK_Cleanup();
console.log("Done");
