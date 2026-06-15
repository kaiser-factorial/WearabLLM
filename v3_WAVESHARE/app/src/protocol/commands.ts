export type LEDCommand =
  | 'GS'
  | 'GP'
  | 'GC'
  | 'RS'
  | 'RF'
  | 'YP'
  | 'BS'
  | 'PS'
  | 'PP';

export const COMMANDS: LEDCommand[] = ['GS', 'GP', 'GC', 'RS', 'RF', 'YP', 'BS', 'PS', 'PP'];

export const COMMAND_DESCRIPTIONS: Record<LEDCommand, string> = {
  GS: 'Yes, confident',
  GP: 'Yes, gentle',
  GC: 'Yes, enthusiastic',
  RS: 'No, firm',
  RF: 'Warning or urgent',
  YP: 'Uncertain',
  BS: 'Neutral info',
  PS: 'Creative',
  PP: 'Philosophical',
};

export const COMMAND_COLORS: Record<LEDCommand, string> = {
  GS: '#22c55e',
  GP: '#22c55e',
  GC: '#22c55e',
  RS: '#ef4444',
  RF: '#ef4444',
  YP: '#eab308',
  BS: '#3b82f6',
  PS: '#a855f7',
  PP: '#a855f7',
};

export function isLEDCommand(value: string): value is LEDCommand {
  return COMMANDS.includes(value as LEDCommand);
}
