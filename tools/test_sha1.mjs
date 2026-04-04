// Test PUB_SHA1Encrypt from the TVT SDK directly
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// Load password from /app/.env (copied into Docker image)
let password = process.env.TVT_PASSWORD;
if (!password) {
    try {
        const env = readFileSync('/app/.env', 'utf-8');
        const m = env.match(/^TVT_PASSWORD=(.+)$/m);
        if (m) password = m[1].trim();
    } catch {}
}
if (!password) { console.error('TVT_PASSWORD not found in env or /app/.env'); process.exit(1); }

const koffi = (await import('koffi')).default;

const libPath = resolve('/app/tvt/bin/linux/libdvrnetsdk.so');
const lib = koffi.load(libPath);

// PUB_SHA1Encrypt(const char* input, int input_len, char* output)
const PUB_SHA1Encrypt = lib.func('PUB_SHA1Encrypt', 'int', ['string', 'int', koffi.out(koffi.pointer('uint8_t'))]);
const output = Buffer.alloc(64); // plenty of space

try {
    const ret = PUB_SHA1Encrypt(password, password.length, output);
    console.log('PUB_SHA1Encrypt return:', ret);
    console.log('Output (first 20 bytes):', output.subarray(0, 20).toString('hex'));
    console.log('Output (first 32 bytes):', output.subarray(0, 32).toString('hex'));
} catch (e) {
    console.error('Error calling PUB_SHA1Encrypt:', e.message);
}

// Also try with padded password (32 bytes)
const pw32 = Buffer.alloc(32);
Buffer.from(password).copy(pw32);
try {
    const ret = PUB_SHA1Encrypt(pw32.toString('binary'), 32, output);
    console.log('\nPUB_SHA1Encrypt(pw32) return:', ret);
    console.log('Output (first 20 bytes):', output.subarray(0, 20).toString('hex'));
} catch (e) {
    console.error('Error:', e.message);
}

// Compare with known nonce if provided via env
const nonce = process.env.TVT_NONCE; // hex string, e.g. "dbc221"
if (nonce) {
    const nonceBuf = Buffer.from(nonce, 'hex');
    const input_with_nonce = Buffer.concat([nonceBuf, Buffer.from(password)]);
    try {
        const ret = PUB_SHA1Encrypt(input_with_nonce.toString('binary'), input_with_nonce.length, output);
        console.log('\nPUB_SHA1Encrypt(nonce+pw) return:', ret);
        console.log('Output:', output.subarray(0, 20).toString('hex'));
    } catch (e) {
        console.error('Error:', e.message);
    }
}

process.exit(0);
