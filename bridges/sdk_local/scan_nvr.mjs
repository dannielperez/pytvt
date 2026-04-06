#!/usr/bin/env node
/**
 * Node.js bridge script that uses the TVT SDK to connect to an NVR
 * and retrieve all programmed camera/IPC information.
 *
 * Usage: node scan_nvr.mjs <ip> <port> <username> <password>
 * Output: JSON to stdout with camera list
 */

import { platform } from 'node:os'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

// Validate args
const [,, ip, port = '6036', username = 'admin', password = ''] = process.argv

if (!ip) {
  console.error(JSON.stringify({ error: 'Usage: node scan_nvr.mjs <ip> [port] [username] [password]' }))
  process.exit(1)
}

// We need koffi for FFI - import dynamically
let koffi
try {
  koffi = (await import('koffi')).default
} catch (e) {
  console.error(JSON.stringify({ error: 'koffi not available. Run: npm install koffi' }))
  process.exit(1)
}

if (platform() !== 'linux') {
  console.error(JSON.stringify({ error: 'TVT SDK requires Linux. Use Docker or run on a Linux machine.' }))
  process.exit(1)
}

// Load the native SDK library
const libPath = resolve(__dirname, 'tvt/bin/linux/libdvrnetsdk.so')
let lib
try {
  lib = koffi.load(libPath)
} catch (e) {
  console.error(JSON.stringify({ error: `Failed to load SDK library: ${e.message}` }))
  process.exit(1)
}

// Define structs matching the SDK
const LPNET_SDK_DEVICEINFO = koffi.struct('LPNET_SDK_DEVICEINFO', {
  localVideoInputNum: 'uchar',
  audioInputNum: 'uchar',
  sensorInputNum: 'uchar',
  sensorOutputNum: 'uchar',
  displayResolutionMask: 'uint',
  videoOuputNum: 'uchar',
  netVideoOutputNum: 'uchar',
  netVideoInputNum: 'uchar',
  IVSNum: 'uchar',
  presetNumOneCH: 'uchar',
  cruiseNumOneCH: 'uchar',
  presetNumOneCruise: 'uchar',
  trackNumOneCH: 'uchar',
  userNum: 'uchar',
  netClientNum: 'uchar',
  netFirstStreamNum: 'uchar',
  deviceType: 'uchar',
  doblueStream: 'uchar',
  audioStream: 'uchar',
  talkAudio: 'uchar',
  bPasswordCheck: 'uchar',
  defBrightness: 'uchar',
  defContrast: 'uchar',
  defSaturation: 'uchar',
  defHue: 'uchar',
  videoInputNum: 'ushort',
  deviceID: 'ushort',
  videoFormat: 'uint',
  function: koffi.array('uint', 8),
  deviceIP: 'uint',
  deviceMAC: koffi.array('uchar', 6),
  devicePort: 'ushort',
  buildDate: 'uint',
  buildTime: 'uint',
  deviceName: koffi.array('char', 36),
  firmwareVersion: koffi.array('char', 36),
  kernelVersion: koffi.array('char', 64),
  hardwareVersion: koffi.array('char', 36),
  MCUVersion: koffi.array('char', 36),
  firmwareVersionEx: koffi.array('char', 64),
  softwareVer: 'uint',
  szSN: koffi.array('char', 32),
  deviceProduct: koffi.array('char', 28)
})

const NET_SDK_IPC_DEVICE_INFO = koffi.struct('NET_SDK_IPC_DEVICE_INFO', {
  deviceID: 'uint',
  channel: 'ushort',
  guid: koffi.array('uchar', 48),
  status: 'ushort',
  szEtherName: koffi.array('char', 16),
  szServer: koffi.array('char', 64),
  nPort: 'ushort',
  nHttpPort: 'ushort',
  nCtrlPort: 'ushort',
  szID: koffi.array('char', 64),
  username: koffi.array('char', 36),
  manufacturerId: 'uint',
  manufacturerName: koffi.array('char', 36),
  productModel: koffi.array('char', 36),
  bUseDefaultCfg: 'uchar',
  bPOEDevice: 'uchar',
  resv: koffi.array('uchar', 2),
  szChlname: koffi.array('char', 36)
})

// Bind SDK functions
const NET_SDK_Init = lib.func('NET_SDK_Init', 'bool', [])
const NET_SDK_Cleanup = lib.func('NET_SDK_Cleanup', 'bool', [])
const NET_SDK_SetConnectTime = lib.func('NET_SDK_SetConnectTime', 'bool', ['uint32_t', 'uint32_t'])
const NET_SDK_Login = lib.func('NET_SDK_Login', 'long', [
  'string', 'uint16_t', 'string', 'string',
  koffi.out(koffi.pointer(LPNET_SDK_DEVICEINFO))
])
const NET_SDK_Logout = lib.func('NET_SDK_Logout', 'bool', ['long'])
const NET_SDK_GetDeviceInfo = lib.func('NET_SDK_GetDeviceInfo', 'bool', [
  'long', koffi.out(koffi.pointer(LPNET_SDK_DEVICEINFO))
])
const NET_SDK_GetDeviceIPCInfo = lib.func('NET_SDK_GetDeviceIPCInfo', 'bool', [
  'long',
  koffi.out(koffi.pointer(NET_SDK_IPC_DEVICE_INFO)),
  'long',
  koffi.out(koffi.pointer('long'))
])
const NET_SDK_GetLastError = lib.func('NET_SDK_GetLastError', 'uint32_t', [])

// Helper to extract null-terminated C string from char array
function cstr(arr) {
  if (typeof arr === 'string') return arr
  if (Array.isArray(arr) || ArrayBuffer.isView(arr)) {
    const bytes = Array.from(arr)
    const nullIdx = bytes.indexOf(0)
    const trimmed = nullIdx >= 0 ? bytes.slice(0, nullIdx) : bytes
    return String.fromCharCode(...trimmed).trim()
  }
  return String(arr).trim()
}

async function scanNVR() {
  const result = {
    nvr_ip: ip,
    nvr_port: parseInt(port),
    success: false,
    device_name: '',
    device_model: '',
    serial_number: '',
    firmware: '',
    total_channels: 0,
    cameras: [],
    error: null
  }

  try {
    // Initialize SDK
    if (!NET_SDK_Init()) {
      throw new Error('Failed to initialize SDK')
    }

    // Set connection timeout (10 seconds, 2 retries)
    NET_SDK_SetConnectTime(10000, 2)

    // Login
    const deviceInfo = {}
    const userId = NET_SDK_Login(ip, parseInt(port), username, password, deviceInfo)

    if (userId === -1) {
      const errCode = NET_SDK_GetLastError()
      throw new Error(`Login failed (error code: ${errCode})`)
    }

    // Get device info
    const devInfo = {}
    NET_SDK_GetDeviceInfo(userId, devInfo)

    result.device_name = cstr(devInfo.deviceName || deviceInfo.deviceName || '')
    result.device_model = cstr(devInfo.deviceProduct || deviceInfo.deviceProduct || '')
    result.serial_number = cstr(devInfo.szSN || deviceInfo.szSN || '')
    result.firmware = cstr(devInfo.firmwareVersion || deviceInfo.firmwareVersion || '')

    // Get IPC (camera) info using raw buffer approach
    const maxCameras = 64
    const structSize = koffi.sizeof(NET_SDK_IPC_DEVICE_INFO)
    const bufSize = maxCameras * structSize
    const ipcBuf = Buffer.alloc(bufSize)
    const ipcCount = [0]

    console.error(`IPC info: structSize=${structSize}, bufSize=${bufSize}`)

    const ipcResult = NET_SDK_GetDeviceIPCInfo(userId, ipcBuf, bufSize, ipcCount)

    if (ipcResult && ipcCount[0] > 0) {
      result.total_channels = ipcCount[0]
      console.error(`IPC count: ${ipcCount[0]}`)

      // Decode each struct from the raw buffer
      for (let i = 0; i < ipcCount[0]; i++) {
        const cam = koffi.decode(ipcBuf, i * structSize, NET_SDK_IPC_DEVICE_INFO)
        if (!cam) continue

        // Debug: print raw field values for first 3 cameras
        if (i < 3) {
          console.error(`\n--- Camera ${i+1} raw fields ---`)
          for (const [key, val] of Object.entries(cam)) {
            const display = Array.isArray(val) || ArrayBuffer.isView(val)
              ? `[${Array.from(val).slice(0, 10).join(',')}${val.length > 10 ? '...' : ''}] (len=${val.length})`
              : val
            console.error(`  ${key}: ${display}`)
          }
        }

        result.cameras.push({
          channel: cam.channel ?? (i + 1),
          name: cstr(cam.szChlname || ''),
          address: cstr(cam.szServer || ''),
          port: cam.nPort || 0,
          status: cam.status === 1 ? 'Online' : 'Offline',
          protocol: cstr(cam.manufacturerName || ''),
          model: cstr(cam.productModel || ''),
          device_id: cam.deviceID || 0
        })
      }
    } else {
      // Fallback: use device info channel count
      const channelCount = devInfo.videoInputNum || deviceInfo.videoInputNum || 0
      result.total_channels = channelCount
      result.error = 'Could not retrieve IPC info, but device is reachable'
    }

    result.success = true

    // Logout
    NET_SDK_Logout(userId)

  } catch (e) {
    result.error = e.message
  } finally {
    try { NET_SDK_Cleanup() } catch (_) {}
  }

  // Output JSON to stdout with sentinels so the Python caller can
  // extract it even if the native SDK pollutes stdout with debug text.
  console.log('___JSON_START___')
  console.log(JSON.stringify(result))
  console.log('___JSON_END___')
}

scanNVR()
