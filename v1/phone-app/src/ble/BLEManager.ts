/**
 * BLE manager — scans for "Wearable LLM" devices advertising Nordic UART
 * Service (NUS), connects, and writes 2-byte commands to the RX characteristic.
 *
 * Works with any microcontroller advertising NUS:
 *   - Circuit Playground Bluefruit (cpb/code.py)
 *   - ESP32-S3 (future)
 */

import { BleManager, Device, State } from 'react-native-ble-plx';
import { Platform } from 'react-native';
import { Buffer } from 'buffer';
import { LEDCommand } from './commands';

// Nordic UART Service UUIDs
const NUS_SERVICE = '6E400001-B5A3-F393-E0A9-E50E24DCCA9E';
const NUS_RX_CHAR = '6E400002-B5A3-F393-E0A9-E50E24DCCA9E'; // phone writes here
const NUS_TX_CHAR = '6E400003-B5A3-F393-E0A9-E50E24DCCA9E'; // phone reads here

// Device name set in cpb/code.py: ble.name = "Wearable LLM"
const DEVICE_NAME = 'Wearable LLM';

export type BLEStatus = 'idle' | 'scanning' | 'connecting' | 'connected' | 'error';

export interface SensorData {
  tempC: number;
  light: number;
  accelX: number;
  accelY: number;
  accelZ: number;
}

/** Parse "VS:22.5,180,0.1,-0.3,9.8" → SensorData or null */
export function parseSensorData(msg: string): SensorData | null {
  if (!msg.startsWith('VS:')) return null;
  const parts = msg.slice(3).split(',');
  if (parts.length !== 5) return null;
  const [t, l, x, y, z] = parts.map(Number);
  if ([t, l, x, y, z].some(isNaN)) return null;
  return { tempC: t, light: l, accelX: x, accelY: y, accelZ: z };
}

class WearableBLEManager {
  private manager: BleManager;
  private device: Device | null = null;
  private statusListeners: ((status: BLEStatus, msg?: string) => void)[] = [];
  private messageListeners: ((message: string) => void)[] = [];
  private rxBuffer = '';  // buffer for reassembling split BLE packets

  constructor() {
    try {
      this.manager = new BleManager();
    } catch (e) {
      console.error('[BLEManager] Failed to initialize BleManager:', e);
      this.manager = null as any;
    }
  }

  onStatusChange(cb: (status: BLEStatus, msg?: string) => void) {
    this.statusListeners.push(cb);
    return () => {
      this.statusListeners = this.statusListeners.filter(l => l !== cb);
    };
  }

  private emit(status: BLEStatus, msg?: string) {
    this.statusListeners.forEach(l => l(status, msg));
  }

  /** Listen for messages sent FROM the device (e.g. button presses) */
  onMessage(cb: (message: string) => void) {
    this.messageListeners.push(cb);
    return () => {
      this.messageListeners = this.messageListeners.filter(l => l !== cb);
    };
  }

  private emitMessage(message: string) {
    this.messageListeners.forEach(l => l(message));
  }

  async checkBluetoothReady(): Promise<boolean> {
    const state = await this.manager.state();
    return state === State.PoweredOn;
  }

  async scan(): Promise<void> {
    const ready = await this.checkBluetoothReady();
    if (!ready) {
      this.emit('error', 'Bluetooth is off. Please enable it.');
      return;
    }

    this.emit('scanning');

    this.manager.startDeviceScan(
      [NUS_SERVICE],
      { allowDuplicates: false },
      (error, scannedDevice) => {
        if (error) {
          this.emit('error', error.message);
          return;
        }
        if (scannedDevice?.name === DEVICE_NAME || scannedDevice?.localName === DEVICE_NAME) {
          this.manager.stopDeviceScan();
          this.connect(scannedDevice);
        }
      }
    );

    // Stop scanning after 15s if nothing found
    setTimeout(() => {
      this.manager.stopDeviceScan();
      if (!this.device) {
        this.emit('idle', 'No device found. Is the CPB powered on?');
      }
    }, 15000);
  }

  private async connect(scannedDevice: Device): Promise<void> {
    this.emit('connecting');
    try {
      this.device = await scannedDevice.connect();
      await this.device.discoverAllServicesAndCharacteristics();

      // Watch for disconnection
      this.device.onDisconnected((_error, _d) => {
        this.device = null;
        this.rxBuffer = '';
        this.emit('idle', 'Device disconnected.');
      });

      // Subscribe to TX characteristic to receive messages from device
      this.device.monitorCharacteristicForService(
        NUS_SERVICE,
        NUS_TX_CHAR,
        (error, char) => {
          if (error) {
            // "Operation was cancelled" is normal on disconnect — ignore it
            if (!error.message?.includes('cancelled')) {
              console.error('[BLEManager] TX monitor error:', error.message);
            }
            return;
          }
          if (char?.value) {
            const decoded = Buffer.from(char.value, 'base64').toString('ascii');
            this.rxBuffer += decoded;
            // Split on newlines — each complete message ends with \n
            const parts = this.rxBuffer.split('\n');
            // Last part is incomplete (no trailing \n yet) — keep it in buffer
            this.rxBuffer = parts.pop() ?? '';
            for (const msg of parts) {
              const trimmed = msg.trim();
              if (trimmed.length > 0) {
                console.log('[BLEManager] Received from device:', trimmed);
                this.emitMessage(trimmed);
              }
            }
          }
        }
      );

      this.emit('connected');
    } catch (e: any) {
      this.device = null;
      this.emit('error', e.message ?? 'Connection failed.');
    }
  }

  async disconnect(): Promise<void> {
    if (this.device) {
      await this.device.cancelConnection();
      this.device = null;
    }
    this.emit('idle');
  }

  get isConnected(): boolean {
    return this.device !== null;
  }

  /**
   * Write a 2-byte LED command to the NUS RX characteristic.
   * e.g. sendCommand('GS') → sends bytes 0x47 0x53 ("GS" in ASCII)
   */
  async sendCommand(cmd: LEDCommand): Promise<void> {
    if (!this.device) throw new Error('Not connected to a device.');

    const bytes = Buffer.from(cmd, 'ascii').toString('base64');
    await this.device.writeCharacteristicWithResponseForService(
      NUS_SERVICE,
      NUS_RX_CHAR,
      bytes
    );
  }

  /** Send an arbitrary string to the device */
  async sendRaw(msg: string): Promise<void> {
    if (!this.device) throw new Error('Not connected to a device.');

    const bytes = Buffer.from(msg, 'ascii').toString('base64');
    await this.device.writeCharacteristicWithResponseForService(
      NUS_SERVICE,
      NUS_RX_CHAR,
      bytes
    );
  }

  destroy() {
    this.manager.destroy();
  }
}

// Singleton — one BLE manager for the whole app
let bleManager: WearableBLEManager;
try {
  bleManager = new WearableBLEManager();
} catch (e) {
  console.error('[BLEManager] Failed to create singleton:', e);
  bleManager = new WearableBLEManager();
}
export { bleManager };
