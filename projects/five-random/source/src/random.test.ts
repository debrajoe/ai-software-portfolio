import { describe, expect, it } from 'vitest';
import { generateNumbers, unbiasedIndex, validateSettings } from './random';

const sequence = (...values: number[]) => { let i = 0; return () => values[i++ % values.length] as number; };

describe('number generation', () => {
  it('generates exactly five values within inclusive boundaries', () => {
    const result = generateNumbers({ min: 1, max: 50, allowDuplicates: true, sortAscending: false }, sequence(0, 49, 1, 48, 2));
    expect(result).toEqual([1, 50, 2, 49, 3]);
  });
  it('generates five unique values when duplicates are off', () => {
    const result = generateNumbers({ min: 10, max: 14, allowDuplicates: false, sortAscending: false }, sequence(0));
    expect(result).toHaveLength(5);
    expect(new Set(result).size).toBe(5);
    expect(result.every(n => n >= 10 && n <= 14)).toBe(true);
  });
  it('sorts only when requested', () => {
    const sorted = generateNumbers({ min: 1, max: 9, allowDuplicates: true, sortAscending: true }, sequence(8, 1, 6, 0, 4));
    expect(sorted).toEqual([1, 2, 5, 7, 9]);
  });
  it('explains invalid whole-number, reversed, and too-small ranges', () => {
    expect(validateSettings({ min: 1.5, max: 10, allowDuplicates: true, sortAscending: true })).toMatch(/whole numbers/);
    expect(validateSettings({ min: 10, max: 1, allowDuplicates: true, sortAscending: true })).toMatch(/minimum/);
    expect(validateSettings({ min: 1, max: 4, allowDuplicates: false, sortAscending: true })).toBe('Choose a range with at least five numbers when duplicates are off.');
    expect(validateSettings({ min: 1, max: 1, allowDuplicates: true, sortAscending: true })).toBeNull();
  });
  it('rejects out-of-bucket values to avoid modulo bias', () => {
    expect(unbiasedIndex(10, sequence(4_294_967_295, 7))).toBe(7);
  });
});
