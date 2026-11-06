import AsyncStorage from '@react-native-async-storage/async-storage';
import type { NumberSettings } from './random';

export type ThemeChoice = 'system' | 'light' | 'dark';
export type SavedSettings = NumberSettings & { theme: ThemeChoice };
export type HistoryItem = {
  id: string;
  numbers: number[];
  createdAt: string;
  min: number;
  max: number;
  allowDuplicates: boolean;
};

const SETTINGS_KEY = '@five-random/settings-v1';
const HISTORY_KEY = '@five-random/history-v1';

export const defaultSettings: SavedSettings = {
  min: 1,
  max: 50,
  allowDuplicates: false,
  sortAscending: true,
  theme: 'system',
};

export async function loadLocalData(): Promise<{ settings: SavedSettings; history: HistoryItem[] }> {
  const [settingsJson, historyJson] = await Promise.all([
    AsyncStorage.getItem(SETTINGS_KEY),
    AsyncStorage.getItem(HISTORY_KEY),
  ]);
  let settings = defaultSettings;
  let history: HistoryItem[] = [];
  try { if (settingsJson) settings = { ...defaultSettings, ...JSON.parse(settingsJson) }; } catch {}
  try { if (historyJson) history = JSON.parse(historyJson).slice(0, 20); } catch {}
  return { settings, history };
}

export const saveSettings = (settings: SavedSettings) => AsyncStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
export const saveHistory = (history: HistoryItem[]) => AsyncStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, 20)));
