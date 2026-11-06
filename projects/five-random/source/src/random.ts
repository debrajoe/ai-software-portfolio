import * as Crypto from 'expo-crypto';

export type NumberSettings = {
  min: number;
  max: number;
  allowDuplicates: boolean;
  sortAscending: boolean;
};

export const RESULT_COUNT = 5;
const UINT32_RANGE = 0x1_0000_0000;

export function validateSettings({ min, max, allowDuplicates }: NumberSettings): string | null {
  if (!Number.isSafeInteger(min) || !Number.isSafeInteger(max)) {
    return 'Enter whole numbers for both the minimum and maximum.';
  }
  if (min > max) return 'The minimum must be less than or equal to the maximum.';
  const size = max - min + 1;
  if (!Number.isSafeInteger(size) || size > UINT32_RANGE) {
    return 'Choose a range containing no more than 4,294,967,296 numbers.';
  }
  if (!allowDuplicates && size < RESULT_COUNT) {
    return 'Choose a range with at least five numbers when duplicates are off.';
  }
  return null;
}

export type RandomUint32 = () => number;

export const secureUint32: RandomUint32 = () => {
  const values = new Uint32Array(1);
  Crypto.getRandomValues(values);
  return values[0] as number;
};

// Rejection sampling avoids the modulo bias introduced by `random % size`.
export function unbiasedIndex(size: number, randomUint32: RandomUint32 = secureUint32): number {
  if (!Number.isInteger(size) || size < 1 || size > UINT32_RANGE) {
    throw new RangeError('size must be an integer from 1 through 2^32');
  }
  const limit = UINT32_RANGE - (UINT32_RANGE % size);
  let value: number;
  do value = randomUint32(); while (value >= limit);
  return value % size;
}

export function generateNumbers(settings: NumberSettings, randomUint32: RandomUint32 = secureUint32): number[] {
  const error = validateSettings(settings);
  if (error) throw new Error(error);
  const size = settings.max - settings.min + 1;
  let results: number[];

  if (settings.allowDuplicates) {
    results = Array.from({ length: RESULT_COUNT }, () => settings.min + unbiasedIndex(size, randomUint32));
  } else {
    // Sparse partial Fisher-Yates: uniform sampling without allocating the whole range.
    const swaps = new Map<number, number>();
    results = [];
    for (let i = 0; i < RESULT_COUNT; i += 1) {
      const chosen = i + unbiasedIndex(size - i, randomUint32);
      const chosenValue = swaps.get(chosen) ?? chosen;
      swaps.set(chosen, swaps.get(i) ?? i);
      results.push(settings.min + chosenValue);
    }
  }

  return settings.sortAscending ? [...results].sort((a, b) => a - b) : results;
}
