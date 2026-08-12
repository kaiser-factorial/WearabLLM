/**
 * 2-byte BLE UART commands that match cpb/code.py RESPONSES dict.
 * The phone writes these to the NUS RX characteristic.
 */

export type LEDCommand =
  | 'GS'  // green solid   — yes, confident
  | 'GP'  // green pulse   — yes, gentle
  | 'GC'  // green chase   — yes, enthusiastic
  | 'RS'  // red solid     — no, firm
  | 'RF'  // red flicker   — warning / urgent
  | 'YP'  // yellow pulse  — uncertain
  | 'BS'  // blue solid    — neutral info
  | 'PS'  // purple solid  — creative, imaginative
  | 'PP'; // purple pulse  — deep, philosophical

export const COMMAND_DESCRIPTIONS: Record<LEDCommand, string> = {
  GS: 'Yes (confident)',
  GP: 'Yes (gentle)',
  GC: 'Yes (enthusiastic)',
  RS: 'No (firm)',
  RF: 'Warning / urgent',
  YP: 'Uncertain',
  BS: 'Neutral info',
  PS: 'Creative',
  PP: 'Philosophical',
};

export function isValidCommand(s: string): s is LEDCommand {
  return ['GS', 'GP', 'GC', 'RS', 'RF', 'YP', 'BS', 'PS', 'PP'].includes(s);
}
