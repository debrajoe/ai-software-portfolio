import { defineConfig } from 'vitest/config';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  resolve: {
    alias: { 'expo-crypto': fileURLToPath(new URL('./src/__mocks__/expo-crypto.ts', import.meta.url)) },
  },
});
