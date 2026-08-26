import assert from 'node:assert/strict';
import { selectionRange } from '../src/offsets.js';

assert.deepEqual(
  selectionRange({ start: 7, end: 14, start_utf16: 8, end_utf16: 15 }),
  [8, 15],
);
assert.deepEqual(selectionRange({ start: 2, end: 5 }), [2, 5]);
